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
        {"type": "aws-gw-ep",   "label": "GW Endpoint",      "icon": "GEP", "desc": "AWS Gateway/Interface VPC Endpoint"},
        {"type": "aws-vgw",     "label": "Virtual Priv GW",  "icon": "VGW", "desc": "AWS Virtual Private Gateway — customer-side attachment for S2S VPN and DX private VIFs"},
        {"type": "aws-cgw",     "label": "Customer GW",      "icon": "CGW", "desc": "AWS Customer Gateway — represents the on-prem endpoint for Site-to-Site VPN"},
        {"type": "aws-vpn-ha",  "label": "VPN HA (A/A)",     "icon": "VHA", "desc": "AWS Site-to-Site VPN HA — active/active dual-tunnel for redundant IPSec connectivity"},
        {"type": "aws-tgw-rt",  "label": "TGW Route Table",  "icon": "TRT", "desc": "AWS Transit Gateway Route Table — explicit route table segment for traffic segmentation"},
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
        {"type": "az-nsg",          "label": "NSG",              "icon": "NSG", "desc": "Azure Network Security Group"},
        {"type": "az-lng",          "label": "Local Net GW",     "icon": "LNG", "desc": "Azure Local Network Gateway — represents on-prem endpoint for S2S VPN connections"},
        {"type": "az-ergw",         "label": "ER Gateway",       "icon": "ERG", "desc": "Azure ExpressRoute Gateway — VNet attachment point for ExpressRoute circuits"},
        {"type": "az-route-server", "label": "Route Server",     "icon": "ARS", "desc": "Azure Route Server — BGP route injection into VNet, enables NVA dynamic routing"},
        {"type": "az-vnet-peer",    "label": "VNet Peering",     "icon": "PER", "desc": "Azure VNet Peering — direct intra or cross-region VNet connectivity (low latency, no GW)"},
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
        {"type": "gcp-router",      "label": "Cloud Router",     "icon": "CR",  "desc": "GCP Cloud Router — BGP peering"},
        {"type": "gcp-classic-vpn", "label": "Classic VPN",      "icon": "CVP", "desc": "GCP Cloud VPN Classic — single-tunnel, static routing, no BGP"},
        {"type": "gcp-ha-vpn",      "label": "HA VPN GW",        "icon": "HVP", "desc": "GCP HA VPN Gateway — dual-interface, BGP, 99.99% SLA for cloud-to-on-prem"},
        {"type": "gcp-ic-partner",  "label": "Partner IC",       "icon": "PIC", "desc": "GCP Partner Interconnect — L2/L3 connectivity via NSP (100 Mbps–50 Gbps)"},
        {"type": "gcp-ncc-spoke",   "label": "NCC Spoke",        "icon": "NSP", "desc": "GCP Network Connectivity Center Spoke — site attachment to NCC hub (VPN/IC/Router)"},
    ],
    "oci": [
        {"type": "oci-vcn", "label": "VCN", "icon": "VCN", "desc": "OCI Virtual Cloud Network"},
        {"type": "oci-subnet", "label": "Subnet", "icon": "SUB", "desc": "OCI VCN Subnet"},
        {"type": "oci-drg", "label": "DRG", "icon": "DRG", "desc": "OCI Dynamic Routing Gateway"},
        {"type": "oci-fc", "label": "FastConnect", "icon": "FC", "desc": "OCI FastConnect — dedicated circuit"},
        {"type": "oci-lb", "label": "Load Balancer", "icon": "OLB", "desc": "OCI Load Balancer"},
        {"type": "oci-waf", "label": "WAF", "icon": "WAF", "desc": "OCI Web Application Firewall"},
        {"type": "oci-nsg",   "label": "NSG",             "icon": "NSG", "desc": "OCI Network Security Group"},
        {"type": "oci-cpe",   "label": "CPE",             "icon": "CPE", "desc": "OCI Customer Premises Equipment — on-prem device representation for IPSec tunnels"},
        {"type": "oci-ipsec", "label": "IPSec Conn",      "icon": "IPS", "desc": "OCI IPSec Connection — tunnel object between CPE and DRG (static or BGP)"},
        {"type": "oci-fc-vc", "label": "FastConnect VC",  "icon": "FVC", "desc": "OCI FastConnect Virtual Circuit — logical VC over FastConnect (private or public peering)"},
    ],
    "ibm": [
        {"type": "ibm-vpc", "label": "VPC", "icon": "VPC", "desc": "IBM Cloud VPC"},
        {"type": "ibm-subnet", "label": "Subnet", "icon": "SUB", "desc": "IBM Cloud VPC Subnet"},
        {"type": "ibm-dl", "label": "Direct Link", "icon": "DL", "desc": "IBM Cloud Direct Link — dedicated circuit"},
        {"type": "ibm-vpn", "label": "VPN Gateway", "icon": "VPN", "desc": "IBM Cloud VPN Gateway"},
        {"type": "ibm-lb", "label": "Load Balancer", "icon": "ILB", "desc": "IBM Cloud Load Balancer"},
        {"type": "ibm-tg",     "label": "Transit GW",    "icon": "TG",  "desc": "IBM Cloud Transit Gateway"},
        {"type": "ibm-dl-ded", "label": "Direct Link Ded","icon": "DLD", "desc": "IBM Direct Link 2.0 Dedicated — physical 1G/10G fiber to IBM PoP"},
        {"type": "ibm-dl-con", "label": "Direct Link Con", "icon": "DLC", "desc": "IBM Direct Link 2.0 Connect — via network service provider (50 Mbps–5 Gbps)"},
    ],
    "multi_cloud": [
        {
            "type": "cloud-peering",
            "label": "Cloud Peering",
            "icon": "PER",
            "desc": "Cross-cloud peering / interconnect",
        },
        {
            "type": "sdwan-overlay",
            "label": "SD-WAN",
            "icon": "SDW",
            "desc": "SD-WAN overlay (Cisco Viptela, VMware VeloCloud, etc.)",
        },
        {
            "type": "sase-pop",
            "label": "SASE PoP",
            "icon": "SSE",
            "desc": "SASE/SSE point of presence (Zscaler, Prisma, etc.)",
        },
        {"type": "internet-exchange", "label": "IXP", "icon": "IXP", "desc": "Internet Exchange Point"},
        {"type": "cloud-region",    "label": "Region",         "icon": "REG", "desc": "Cloud region/availability zone boundary"},
        {"type": "megaport-mcr",    "label": "Megaport MCR",   "icon": "MCR", "desc": "Megaport Cloud Router — cloud-neutral L2/L3 interconnect between CSPs and on-prem"},
        {"type": "equinix-fabric",  "label": "Equinix Fabric", "icon": "EQX", "desc": "Equinix Fabric — colocation-based cloud interconnect for CSP-to-CSP and on-prem"},
    ],
    "dod": [
        {"type": "dod-bcap",          "label": "BCAP",           "icon": "BCP", "desc": "Boundary Cloud Access Point — DISA-managed DISN boundary protection (SCCA FRD §2.1.1)"},
        {"type": "dod-vdss",          "label": "VDSS Stack",     "icon": "VDS", "desc": "Virtual DC Security Stack — WAF/IDS-IPS/TLS inspection/PPSM (SCCA FRD §2.1.2)"},
        {"type": "dod-vdms",          "label": "VDMS Stack",     "icon": "VDM", "desc": "Virtual DC Managed Services — ACAS/HBSS/patch/logging/directory (SCCA FRD §2.1.3)"},
        {"type": "dod-tccm",          "label": "TCCM",           "icon": "TCM", "desc": "Trusted Cloud Credential Manager — IAM/RBAC/CAC-PIV/CCMP (SCCA FRD §2.1.4)"},
        {"type": "dod-niprnet-onramp","label": "NIPRNet On-ramp","icon": "NPR", "desc": "DISA NIPRNet Cloud On-ramp — DISN-managed cloud entry point for DoD organizations"},
        # JWICS (Joint Worldwide Intelligence Communications System)
        {"type": "dod-jwics-backbone",    "label": "JWICS Backbone",       "icon": "JWX", "desc": "DIA-managed JWICS backbone circuit — SECRET/TS classification, NSA Type 1 encrypted, physically separated from NIPRNet"},
        {"type": "dod-jwics-gateway",     "label": "JWICS Gateway",        "icon": "JWG", "desc": "Agency JWICS gateway router — ingress/egress from SCIF LAN to JWICS backbone; DISA-approved platform"},
        {"type": "dod-jwics-dns",         "label": "JWICS DNS",            "icon": "JDN", "desc": "JWICS recursive DNS resolver (DIA-managed) — isolated from Internet/NIPRNet DNS, authoritative for .jwics.gov and .dia.smil.mil zones"},
        {"type": "dod-jwics-mail-relay",  "label": "JWICS Mail Relay",     "icon": "JMR", "desc": "JWICS SMTP relay — mandatory S/MIME with NSS PKI cert, HBSS content scan at each hop, no Internet relay path"},
        {"type": "dod-type1-encryptor",   "label": "Type 1 Encryptor",     "icon": "T1E", "desc": "NSA Type 1 encryption device (KG-250A, KIV-7M, TACLANE Flex) — required on all JWICS/SIPR circuits; key fill via KYK-13 or Simple Key Loader"},
        {"type": "dod-scif-lan",          "label": "SCIF LAN",             "icon": "SCF", "desc": "Sensitive Compartmented Information Facility LAN — physically isolated, NSS PKI-only, CAC+PIN required, no removable media"},
        # C2S — AWS Secret Region (Commercial Cloud Services, classified)
        {"type": "dod-c2s-direct-connect","label": "C2S ClassifiedConnect","icon": "C2D", "desc": "AWS C2S ClassifiedConnect — 1G/10G dedicated circuit from DISA Secret CAP to AWS Secret Region (us-gov-secret-1); no Internet path"},
        {"type": "dod-c2s-tgw",          "label": "C2S Transit Gateway",  "icon": "C2T", "desc": "AWS C2S Transit Gateway — central routing hub for VPC attachments in AWS Secret Region; BGP route propagation"},
        {"type": "dod-c2s-vpc",          "label": "C2S VPC",              "icon": "C2V", "desc": "AWS C2S Virtual Private Cloud — IL5/IL6 workload isolation in AWS Secret Region; VPC Flow Logs and GuardDuty mandatory"},
        {"type": "dod-c2s-dns-phz",      "label": "C2S Route 53 PHZ",     "icon": "C2Z", "desc": "C2S Route 53 Private Hosted Zone — classified DNS for .c2s.ic.gov; conditional forwarder to DISA DNS via ClassifiedConnect for .smil.mil"},
        # C2E — Azure Government Secret (Commercial Cloud Enterprise, classified)
        {"type": "dod-c2e-expressroute",  "label": "C2E ExpressRoute",     "icon": "C2X", "desc": "Azure C2E ExpressRoute — dedicated circuit from DISA Secret CAP to Azure Government Secret; NSA Type 1 on physical layer"},
        {"type": "dod-c2e-vnet",         "label": "C2E VNet",             "icon": "C2N", "desc": "Azure C2E Virtual Network — IL5/IL6 workload isolation in Azure Government Secret; Azure Firewall Premium + Defender for Cloud mandatory"},
        {"type": "dod-c2e-dns-private",  "label": "C2E Private DNS Zone", "icon": "C2P", "desc": "C2E Azure Private DNS Zone — classified name resolution; Azure Private Resolver with conditional forwarder to DISA DNS via ExpressRoute"},
        # Shared DISA secret-side components
        {"type": "dod-secret-bcap",      "label": "Secret BCAP/CAP",      "icon": "SBC", "desc": "DISA Classified Cloud Access Point (Secret) — SECRET-side boundary between JWICS and C2S/C2E; applies full SCCA inspection chain at SECRET classification level"},
        {"type": "dod-cds",              "label": "Cross-Domain Solution", "icon": "CDS", "desc": "Cross-Domain Solution (CDS) — hardware-enforced data guard between NIPR↔SIPR or SIPR↔JWICS; NSA-evaluated (Forcepoint Trusted Gateway, Owl Cyber Defense, Everfox High Speed Guard); filter policy is allowlist-only"},
    ],
    "colocation": [
        {
            "type": "meet-me-room",
            "label": "Meet-Me Room",
            "icon": "MMR",
            "desc": "Carrier-neutral meet-me room — physical handoff between customer and CSP/carrier",
        },
        {
            "type": "cross-connect",
            "label": "Cross-Connect",
            "icon": "XX",
            "desc": "Physical cross-connect cable between racks/cages in a colocation facility",
        },
        {
            "type": "demarc",
            "label": "Demarc",
            "icon": "DM",
            "desc": "Demarcation point — boundary between carrier and customer responsibility",
        },
        {
            "type": "cage",
            "label": "Cage/Suite",
            "icon": "CGE",
            "desc": "Colocation cage or private suite housing customer equipment",
        },
        {"type": "cabinet", "label": "Cabinet", "icon": "CAB", "desc": "Equipment cabinet/rack in colocation facility"},
    ],
    "vdi": [
        # VDI Infrastructure
        {
            "type": "vdi-session-host",
            "label": "Session Host",
            "icon": "SH",
            "desc": "VDI Session Host (RDSH/AVD/Citrix VDA)",
        },
        {
            "type": "vdi-connection-broker",
            "label": "Connection Broker",
            "icon": "CB",
            "desc": "VDI Connection Broker (RDS Broker/Citrix Controller/Horizon CS)",
        },
        {
            "type": "vdi-gateway",
            "label": "VDI Gateway",
            "icon": "VGW",
            "desc": "Remote access gateway (RD Gateway/Citrix Gateway/UAG)",
        },
        {
            "type": "vdi-profile-server",
            "label": "Profile Server",
            "icon": "PFS",
            "desc": "User profile storage (FSLogix/Citrix UPM/VMware DEM)",
        },
        {
            "type": "vdi-gpu-host",
            "label": "GPU Host",
            "icon": "GPU",
            "desc": "GPU-enabled host (vGPU/GPU passthrough for CAD/GIS)",
        },
        {
            "type": "vdi-license-server",
            "label": "License Server",
            "icon": "LIC",
            "desc": "VDI license server (RDS CAL/Citrix/Horizon)",
        },
        {
            "type": "vdi-image-store",
            "label": "Image Store",
            "icon": "IMG",
            "desc": "Golden image repository (Azure Compute Gallery/vSphere Content Library)",
        },
        # Endpoints
        {
            "type": "thin-client",
            "label": "Thin Client",
            "icon": "TC",
            "desc": "Thin client endpoint (IGEL/HP/Dell Wyse)",
        },
        {
            "type": "zero-client",
            "label": "Zero Client",
            "icon": "ZC",
            "desc": "Zero client (Teradici PCoIP/Dell Wyse 3040)",
        },
        {
            "type": "vdi-web-client",
            "label": "Web Client",
            "icon": "WC",
            "desc": "Browser-based VDI access (HTML5/RD Web)",
        },
        # Cloud VDI Services
        {
            "type": "avd-hostpool",
            "label": "AVD Host Pool",
            "icon": "AHP",
            "desc": "Azure Virtual Desktop host pool (pooled/personal)",
        },
        {
            "type": "avd-workspace",
            "label": "AVD Workspace",
            "icon": "AWS",
            "desc": "Azure Virtual Desktop workspace + application groups",
        },
        {
            "type": "aws-workspaces",
            "label": "WorkSpaces",
            "icon": "WKS",
            "desc": "Amazon WorkSpaces — managed DaaS (PCoIP/WSP)",
        },
        {
            "type": "aws-appstream",
            "label": "AppStream 2.0",
            "icon": "AS2",
            "desc": "Amazon AppStream 2.0 — app streaming",
        },
        {
            "type": "gcp-vdi",
            "label": "GCP VDI",
            "icon": "GVD",
            "desc": "GCP VDI on Sole-Tenant Nodes / Chrome Enterprise",
        },
        {
            "type": "citrix-cloud",
            "label": "Citrix Cloud",
            "icon": "CTX",
            "desc": "Citrix DaaS — cloud-hosted delivery controller",
        },
        {"type": "horizon-cloud", "label": "Horizon Cloud", "icon": "HZN", "desc": "VMware Horizon Cloud on Azure/AWS"},
    ],
    "edge_compute": [
        {
            "type": "edge-gateway",
            "label": "Edge Gateway",
            "icon": "EGW",
            "desc": "IoT/MEC edge gateway — local compute + store-and-forward",
        },
        {
            "type": "edge-cluster",
            "label": "Edge Cluster",
            "icon": "ECL",
            "desc": "Edge K3s/MicroK8s cluster for DDIL operations",
        },
        {
            "type": "mec-node",
            "label": "MEC Node",
            "icon": "MEC",
            "desc": "Multi-access Edge Computing node (5G/LTE edge)",
        },
        {
            "type": "fog-node",
            "label": "Fog Node",
            "icon": "FOG",
            "desc": "Fog computing node — intermediate processing between edge and cloud",
        },
        {"type": "cdn-pop", "label": "CDN PoP", "icon": "CDN", "desc": "Content Delivery Network point of presence"},
        {"type": "kiosk", "label": "Kiosk", "icon": "KSK", "desc": "Public-facing kiosk/digital signage endpoint"},
    ],
    "digital_twin": [
        {
            "type": "twin-network",
            "label": "Network Twin",
            "icon": "NT",
            "desc": "Forward Networks-style network digital twin — snapshots topology (devices, links, ACLs, routing tables) for intent validation and what-if simulation",
        },
        {
            "type": "twin-intent-validator",
            "label": "Intent Validator",
            "icon": "IV",
            "desc": "Validates reachability, ACL compliance, and IL boundary isolation intent rules against proposed topology changes",
        },
        {
            "type": "twin-blast-radius",
            "label": "Blast Radius Analyzer",
            "icon": "BR",
            "desc": "Identifies downstream systems impacted by failure of any device or link in the proposed topology change",
        },
        {
            "type": "twin-topo-simulator",
            "label": "Topology Simulator",
            "icon": "TS",
            "desc": "Simulates proposed network topology delta — emits PASS/WARN/FAIL verdict before pushing device configs",
        },
    ],
}

# ── Extended Cloud Networking Objects (Well-Architected Hybrid Networking) ────
# Adds missing services identified from AWS Well-Architected Hybrid Networking
# Lens and multi-CSP equivalence research.
CLOUD_OBJECTS_EXTENDED = {
    "aws": [
        {
            "type": "aws-dx-gw",
            "label": "DX Gateway",
            "icon": "DGW",
            "desc": "AWS Direct Connect Gateway — global resource for multi-Region DX access (up to 20 VGWs, 6 TGWs)",
        },
        {
            "type": "aws-privatelink",
            "label": "PrivateLink",
            "icon": "PL",
            "desc": "AWS PrivateLink — private endpoint for AWS/custom services via ENI in VPC",
        },
        {
            "type": "aws-cloudwan",
            "label": "Cloud WAN",
            "icon": "CWN",
            "desc": "AWS Cloud WAN — global network with policy-based segmentation",
        },
        {
            "type": "aws-ga",
            "label": "Global Accelerator",
            "icon": "GA",
            "desc": "AWS Global Accelerator — anycast IPs for TCP/UDP optimization via AWS backbone",
        },
        {
            "type": "aws-shield",
            "label": "Shield",
            "icon": "SHD",
            "desc": "AWS Shield — DDoS protection (Standard=free, Advanced=$3k/mo)",
        },
        {
            "type": "aws-netmgr",
            "label": "Network Manager",
            "icon": "NMG",
            "desc": "AWS Network Manager — centralized network monitoring and route analysis",
        },
        {
            "type": "aws-flowlogs",
            "label": "Flow Logs",
            "icon": "FLG",
            "desc": "VPC Flow Logs — capture IP traffic metadata to S3/CloudWatch Logs",
        },
        {
            "type": "aws-reach",
            "label": "Reachability Analyzer",
            "icon": "RCH",
            "desc": "VPC Reachability Analyzer — automated network path analysis",
        },
        {
            "type": "aws-gwlb",
            "label": "Gateway LB",
            "icon": "GLB",
            "desc": "AWS Gateway Load Balancer — transparent inline inspection (L3 bump-in-wire)",
        },
        {
            "type": "aws-localzone",
            "label": "Local Zone",
            "icon": "LZ",
            "desc": "AWS Local Zone — edge compute for ultra-low latency (<10ms)",
        },
        {
            "type": "aws-outpost",
            "label": "Outposts",
            "icon": "OP",
            "desc": "AWS Outposts — AWS infrastructure on-premises (hybrid cloud)",
        },
        {
            "type": "aws-guardduty",
            "label": "GuardDuty",
            "icon": "GD",
            "desc": "AWS GuardDuty — threat detection across EC2/IAM/S3/network",
        },
        {
            "type": "aws-securityhub",
            "label": "Security Hub",
            "icon": "SH",
            "desc": "AWS Security Hub — centralized compliance and findings aggregator",
        },
        {
            "type": "aws-inspector",
            "label": "Inspector",
            "icon": "INS",
            "desc": "Amazon Inspector — automated vulnerability scanning (OS/network)",
        },
        {
            "type": "aws-config",
            "label": "Config",
            "icon": "CFG",
            "desc": "AWS Config — resource compliance tracking and drift detection",
        },
        {
            "type": "aws-ad",
            "label": "Managed AD",
            "icon": "AD",
            "desc": "AWS Managed Microsoft AD — CAC/PIV/PKI integration for DoD",
        },
        {
            "type": "aws-kms",
            "label": "KMS",
            "icon": "KMS",
            "desc": "AWS KMS — FIPS 140-2 key management with CMK support",
        },
        {
            "type": "aws-privateca",
            "label": "Private CA",
            "icon": "PCA",
            "desc": "AWS Private CA — internal PKI for TLS/mutual TLS certificates",
        },
        {
            "type": "aws-ssm",
            "label": "Systems Mgr",
            "icon": "SSM",
            "desc": "AWS Systems Manager — patch management, config compliance, remote access",
        },
        {
            "type": "aws-ct",
            "label": "CloudTrail",
            "icon": "CT",
            "desc": "AWS CloudTrail — API audit logging with org trail support",
        },
        {
            "type": "aws-idc",
            "label": "IAM Identity Center",
            "icon": "IDC",
            "desc": "AWS IAM Identity Center — SSO/RBAC for multi-account",
        },
    ],
    "azure": [
        {
            "type": "az-er-global",
            "label": "ER Global Reach",
            "icon": "EGR",
            "desc": "Azure ExpressRoute Global Reach — connect on-prem sites via Microsoft backbone",
        },
        {
            "type": "az-privatelink",
            "label": "Private Link",
            "icon": "PL",
            "desc": "Azure Private Link — private endpoint for Azure/custom services",
        },
        {
            "type": "az-ddos",
            "label": "DDoS Protection",
            "icon": "DDP",
            "desc": "Azure DDoS Protection Standard (~$2.9k/mo)",
        },
        {
            "type": "az-netwatcher",
            "label": "Network Watcher",
            "icon": "NW",
            "desc": "Azure Network Watcher — topology, connectivity checks, NSG diagnostics",
        },
        {
            "type": "az-flowlogs",
            "label": "VNet Flow Logs",
            "icon": "FLG",
            "desc": "Azure VNet Flow Logs — VNet-level traffic capture with analytics",
        },
        {
            "type": "az-stack",
            "label": "Stack HCI",
            "icon": "HCI",
            "desc": "Azure Stack HCI — hyperconverged Azure on-premises",
        },
        {
            "type": "az-crosslb",
            "label": "Cross-region LB",
            "icon": "XLB",
            "desc": "Azure Cross-region Load Balancer — global L4 load balancing",
        },
        {
            "type": "az-defender",
            "label": "Defender",
            "icon": "DEF",
            "desc": "Microsoft Defender for Cloud — CSPM + workload protection",
        },
        {
            "type": "az-sentinel",
            "label": "Sentinel",
            "icon": "SEN",
            "desc": "Microsoft Sentinel — cloud-native SIEM/SOAR",
        },
        {
            "type": "az-keyvault",
            "label": "Key Vault",
            "icon": "KV",
            "desc": "Azure Key Vault — FIPS 140-2 L2/L3 key and secret management",
        },
        {
            "type": "az-entra",
            "label": "Entra ID",
            "icon": "EID",
            "desc": "Microsoft Entra ID — identity platform with CAC/PIV certificate auth",
        },
        {
            "type": "az-monitor",
            "label": "Monitor",
            "icon": "MON",
            "desc": "Azure Monitor — metrics, logs, diagnostics for all resources",
        },
        {
            "type": "az-policy",
            "label": "Policy",
            "icon": "POL",
            "desc": "Azure Policy — governance guardrails at management group scope",
        },
    ],
    "gcp": [
        {
            "type": "gcp-psc",
            "label": "Private Svc Connect",
            "icon": "PSC",
            "desc": "GCP Private Service Connect — private access to Google/custom services",
        },
        {
            "type": "gcp-ncc",
            "label": "Network CC",
            "icon": "NCC",
            "desc": "GCP Network Connectivity Center — global hub for hybrid/multi-cloud spokes",
        },
        {
            "type": "gcp-nic",
            "label": "Network Intel",
            "icon": "NIC",
            "desc": "GCP Network Intelligence Center — topology, performance, firewall insights",
        },
        {
            "type": "gcp-gfe",
            "label": "Global LB",
            "icon": "GFE",
            "desc": "GCP Global External Application LB — single anycast IP across all regions",
        },
        {
            "type": "gcp-gdc",
            "label": "Distributed Cloud",
            "icon": "GDC",
            "desc": "Google Distributed Cloud — sovereign/air-gapped deployment (IL6/SECRET)",
        },
        {
            "type": "gcp-flowlogs",
            "label": "Flow Logs",
            "icon": "FLG",
            "desc": "GCP VPC Flow Logs — configurable sampling rate, direct BigQuery export",
        },
        {
            "type": "gcp-scc",
            "label": "Security CC",
            "icon": "SCC",
            "desc": "GCP Security Command Center — threat detection and compliance",
        },
        {
            "type": "gcp-kms",
            "label": "Cloud KMS",
            "icon": "KMS",
            "desc": "GCP Cloud KMS + Cloud HSM — FIPS 140-2 L3 key management",
        },
        {
            "type": "gcp-assured",
            "label": "Assured Workloads",
            "icon": "AW",
            "desc": "GCP Assured Workloads — IL4/IL5 compliance controls via org policies",
        },
        {
            "type": "gcp-orgpolicy",
            "label": "Org Policy",
            "icon": "OPL",
            "desc": "GCP Organization Policy — resource constraints and governance",
        },
    ],
    "oci": [
        {
            "type": "oci-ddos",
            "label": "DDoS Protection",
            "icon": "DDP",
            "desc": "OCI DDoS Protection — always-on, FREE for all customers (unique among CSPs)",
        },
        {
            "type": "oci-pathanalyzer",
            "label": "Path Analyzer",
            "icon": "PA",
            "desc": "OCI Network Path Analyzer — automated reachability analysis",
        },
        {
            "type": "oci-flowlogs",
            "label": "Flow Logs",
            "icon": "FLG",
            "desc": "OCI VCN Flow Logs — subnet or VNIC level capture",
        },
        {
            "type": "oci-dedicated",
            "label": "Dedicated Region",
            "icon": "DR",
            "desc": "OCI Dedicated Region Cloud@Customer — full OCI on-premises",
        },
        {
            "type": "oci-fd",
            "label": "Fault Domain",
            "icon": "FD",
            "desc": "OCI Fault Domain — three-tier isolation (Region > AD > FD)",
        },
        {
            "type": "oci-cloudguard",
            "label": "Cloud Guard",
            "icon": "CG",
            "desc": "OCI Cloud Guard — threat detection, security zones, responder recipes",
        },
        {
            "type": "oci-vault",
            "label": "Vault",
            "icon": "VLT",
            "desc": "OCI Vault — HSM-backed key management (FIPS 140-2 L3, ~$11K/mo dedicated)",
        },
        {
            "type": "oci-vss",
            "label": "Vuln Scanning",
            "icon": "VSS",
            "desc": "OCI Vulnerability Scanning Service — host/container scanning",
        },
        {
            "type": "oci-identity",
            "label": "Identity Domains",
            "icon": "IDM",
            "desc": "OCI Identity Domains — CAC/PIV via X.509 + SAML federation",
        },
        {
            "type": "oci-audit",
            "label": "Audit",
            "icon": "AUD",
            "desc": "OCI Audit — immutable API activity log (NIST AU controls)",
        },
        {
            "type": "oci-nfw",
            "label": "Network Firewall",
            "icon": "NFW",
            "desc": "OCI Network Firewall — Palo Alto PaaS, IDS/IPS + threat prevention (~$3.6K/mo)",
        },
    ],
    "ibm": [
        {
            "type": "ibm-satellite",
            "label": "Satellite",
            "icon": "SAT",
            "desc": "IBM Cloud Satellite — extend IBM services to any infrastructure",
        },
        {
            "type": "ibm-cis",
            "label": "CIS",
            "icon": "CIS",
            "desc": "IBM Cloud Internet Services — Cloudflare-powered CDN/WAF/DDoS/DNS",
        },
        {
            "type": "ibm-flowlogs",
            "label": "Flow Logs",
            "icon": "FLG",
            "desc": "IBM Cloud Flow Logs for VPC — instance NIC or subnet capture",
        },
        {
            "type": "ibm-scc",
            "label": "SCC",
            "icon": "SCC",
            "desc": "IBM Security & Compliance Center — posture monitoring with profiles",
        },
        {
            "type": "ibm-keyprotect",
            "label": "Key Protect",
            "icon": "KP",
            "desc": "IBM Key Protect — managed key lifecycle with BYOK support",
        },
        {
            "type": "ibm-hpcs",
            "label": "HPCS",
            "icon": "HSM",
            "desc": "IBM Hyper Protect Crypto Services — FIPS 140-2 L4 HSM (highest available)",
        },
        {
            "type": "ibm-appid",
            "label": "App ID",
            "icon": "AID",
            "desc": "IBM Cloud App ID — identity and access management with SAML federation",
        },
    ],
}

# Merge extended objects into primary palette (3-line loop; keeps separation for audits)
for _csp_ext, _nodes_ext in CLOUD_OBJECTS_EXTENDED.items():
    CLOUD_OBJECTS.setdefault(_csp_ext, []).extend(_nodes_ext)

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
        "gcp": {
            "service": "Cloud Interconnect (Dedicated)",
            "type": "gcp-ic",
            "speeds": "10/100 Gbps",
            "parity": "full",
        },
        "oci": {
            "service": "FastConnect",
            "type": "oci-fc",
            "speeds": "1/10/100 Gbps",
            "parity": "full",
            "note": "FREE egress over FastConnect — unique among CSPs",
        },
        "ibm": {"service": "Direct Link", "type": "ibm-dl", "speeds": "1/2/5/10 Gbps", "parity": "partial"},
    },
    "site_to_site_vpn": {
        "category": "Connectivity",
        "description": "IPSec VPN tunnel over internet",
        "aws": {"service": "Site-to-Site VPN", "type": "aws-vpn", "throughput": "1.25 Gbps/tunnel"},
        "azure": {"service": "VPN Gateway", "type": "az-vpn-gw", "throughput": "Up to 10 Gbps", "parity": "full"},
        "gcp": {"service": "Cloud VPN (HA VPN)", "type": "gcp-vpn", "throughput": "3 Gbps/tunnel", "parity": "full"},
        "oci": {"service": "Site-to-Site VPN", "throughput": "250 Mbps/tunnel", "parity": "partial"},
        "ibm": {
            "service": "VPN for VPC",
            "type": "ibm-vpn",
            "throughput": "650 Mbps",
            "parity": "partial",
            "note": "No BGP — static routes only",
        },
    },
    "transit_hub": {
        "category": "Connectivity",
        "description": "Hub-and-spoke network transit for multi-VPC connectivity",
        "aws": {"service": "Transit Gateway", "type": "aws-tgw", "scope": "Regional"},
        "azure": {
            "service": "Virtual WAN",
            "type": "az-vwan",
            "scope": "Global",
            "parity": "full+",
            "note": "Natively global with integrated SD-WAN partner NVAs",
        },
        "gcp": {"service": "Network Connectivity Center", "type": "gcp-ncc", "scope": "Global", "parity": "full"},
        "oci": {
            "service": "DRG v2",
            "type": "oci-drg",
            "scope": "Regional",
            "parity": "full",
            "note": "Free, supports transitive peering natively",
        },
        "ibm": {"service": "Transit Gateway", "type": "ibm-tg", "scope": "Regional", "parity": "partial"},
    },
    "private_endpoint": {
        "category": "Connectivity",
        "description": "Private access to cloud services without internet exposure",
        "aws": {"service": "PrivateLink", "type": "aws-privatelink"},
        "azure": {
            "service": "Private Link",
            "type": "az-privatelink",
            "parity": "full",
            "note": "Deepest PaaS integration — nearly every Azure service supported",
        },
        "gcp": {"service": "Private Service Connect", "type": "gcp-psc", "parity": "full"},
        "oci": {
            "service": "Service Gateway / Private Endpoint",
            "parity": "partial",
            "note": "Primarily Oracle services only",
        },
        "ibm": {"service": "Virtual Private Endpoints", "parity": "partial"},
    },
    "global_wan": {
        "category": "Connectivity",
        "description": "Global WAN orchestration with policy-based routing",
        "aws": {"service": "Cloud WAN", "type": "aws-cloudwan"},
        "azure": {
            "service": "Virtual WAN",
            "type": "az-vwan",
            "parity": "full+",
            "note": "Built-in SD-WAN partner NVA integration",
        },
        "gcp": {"service": "Network Connectivity Center", "type": "gcp-ncc", "parity": "full"},
        "oci": {"parity": "none"},
        "ibm": {"parity": "none"},
    },
    "virtual_network": {
        "category": "Architecture",
        "description": "Isolated virtual network",
        "aws": {"service": "VPC", "type": "aws-vpc", "scope": "Regional"},
        "azure": {"service": "VNet", "type": "az-vnet", "scope": "Regional", "parity": "full"},
        "gcp": {
            "service": "VPC",
            "type": "gcp-vpc",
            "scope": "Global",
            "parity": "full+",
            "note": "Global VPCs — subnets span regions, eliminates cross-region peering",
        },
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
        "oci": {
            "service": "DDoS Protection",
            "type": "oci-ddos",
            "cost": "FREE",
            "parity": "full+",
            "note": "Enterprise-grade DDoS included at no cost for all customers",
        },
        "ibm": {"service": "CIS (Cloudflare)", "type": "ibm-cis", "parity": "full"},
    },
    "flow_logs": {
        "category": "Monitoring",
        "description": "Network traffic flow metadata capture",
        "aws": {"service": "VPC Flow Logs", "type": "aws-flowlogs"},
        "azure": {"service": "VNet Flow Logs", "type": "az-flowlogs", "parity": "full"},
        "gcp": {
            "service": "VPC Flow Logs",
            "type": "gcp-flowlogs",
            "parity": "full",
            "note": "Configurable sampling rate + direct BigQuery export",
        },
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
        "azure": {
            "service": "Front Door",
            "type": "az-front",
            "parity": "full+",
            "note": "Unified global L7 LB + CDN + WAF",
        },
        "gcp": {
            "service": "Global External Application LB",
            "type": "gcp-gfe",
            "parity": "full+",
            "note": "Single anycast IP across all regions — no DNS-based failover needed",
        },
        "oci": {"parity": "none", "note": "DNS-based only"},
        "ibm": {"service": "CIS GLB (Cloudflare)", "type": "ibm-cis", "parity": "partial"},
    },
    "hybrid_edge": {
        "category": "Architecture",
        "description": "Cloud infrastructure deployed on-premises (hybrid edge)",
        "aws": {"service": "Outposts / Local Zones", "type": "aws-outpost"},
        "azure": {"service": "Stack HCI / Arc", "type": "az-stack", "parity": "full"},
        "gcp": {
            "service": "Distributed Cloud (GDC)",
            "type": "gcp-gdc",
            "parity": "full",
            "note": "Air-gapped sovereign deployment for IL6/SECRET",
        },
        "oci": {"service": "Dedicated Region Cloud@Customer", "type": "oci-dedicated", "parity": "full"},
        "ibm": {
            "service": "Satellite",
            "type": "ibm-satellite",
            "parity": "full",
            "note": "Most flexible — extends IBM services to any infrastructure",
        },
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
    "aws": {"10g": 1638, "100g": 16380},
    "azure": {"10g": 1700, "100g": 17000},
    "gcp": {"10g": 1700, "100g": 17000},
    "oci": {"10g": 438, "100g": 4380, "note": "~75% cheaper than AWS/Azure/GCP"},
    "ibm": {"10g": 1800},
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
    "aws_vpn_concentrator": {
        "per_site_mbps": 100,
        "min_sites": 25,
        "note": "Managed VPN concentrator for 25+ remote sites",
    },
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
        "description": "Managed VPN concentrator attachment to TGW for many remote sites at 50-100 Mbps each.",
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
    {
        "id": "AP-001",
        "title": "DX locations not in Associated Region",
        "description": "Using DX locations that are not co-located with the workload AWS Region — will NOT qualify for 99.99% SLA even with redundant connections",
        "severity": "high",
        "recommendation": "Ensure at least one DX location is in the Associated Region",
    },
    {
        "id": "AP-002",
        "title": "Backup DX Gateway for resilience",
        "description": "DXGW is a configuration overlay, not infrastructure — adding a backup DXGW does NOT improve availability",
        "severity": "medium",
        "recommendation": "Focus on diverse DX locations instead of backup DXGW",
    },
    {
        "id": "AP-003",
        "title": "Backup Transit Gateway for data plane",
        "description": "TGW uses Hyperplane distributed nodes with 99.99% SLA — a backup TGW does NOT improve data plane resilience",
        "severity": "medium",
        "recommendation": "Use Dev TGW only for management plane safety (config testing via CI/CD)",
    },
    {
        "id": "AP-004",
        "title": "BGP-only failover without BFD",
        "description": "Default BGP hold timer is 90 seconds — unacceptable for production. BFD detects failure in <1 second",
        "severity": "high",
        "recommendation": "Always enable BFD on all DX/ER virtual interfaces",
    },
    {
        "id": "AP-005",
        "title": "Cross-zone LB enabled on NLB for resilience",
        "description": "Counter-intuitively, enabling cross-zone LB on NLB REDUCES resilience — a failed AZ drags down healthy AZs instead of being cleanly removed",
        "severity": "medium",
        "recommendation": "Disable cross-zone LB; use Route 53 health checks to remove failed AZ endpoints",
    },
    {
        "id": "AP-006",
        "title": "LAG for high availability",
        "description": "LAG terminates on a SINGLE AWS device — NOT suitable for HA. All connections share one device SPOF",
        "severity": "high",
        "recommendation": "Use separate connections at diverse locations instead of LAG for HA",
    },
    {
        "id": "AP-007",
        "title": "Customer-side policing only for noisy neighbor",
        "description": "Policing on customer router only works on ingress — does NOT protect from UDP floods originating from AWS side",
        "severity": "medium",
        "recommendation": "Use Transit VIF + GRE tunnels with separate TGW route tables for hard traffic isolation",
    },
    {
        "id": "AP-008",
        "title": "Single-VIF DX without VPN backup",
        "description": "A single DX VIF with no VPN backup has no failover path — internet VPN should always be configured as backup",
        "severity": "high",
        "recommendation": "Configure Site-to-Site VPN as automatic DX backup",
    },
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
    "dod": [
        {"type": "dod-niprnet-onramp", "label": "NIPRNet On-ramp", "dx": 40,  "dy": 60},
        {"type": "dod-bcap",           "label": "BCAP",             "dx": 200, "dy": 60},
        {"type": "dod-vdss",           "label": "VDSS Stack",       "dx": 360, "dy": 60},
        {"type": "dod-vdms",           "label": "VDMS Stack",       "dx": 200, "dy": 150},
        {"type": "dod-tccm",           "label": "TCCM",             "dx": 360, "dy": 150},
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
    {
        "id": "NET-ENC-001",
        "title": "WAN links require encryption",
        "severity": "CAT1",
        "category": "encryption",
        "regimes": ["fisma_high", "stig", "fips", "cjis", "icd503", "cnss1253"],
        "description": "All WAN/inter-site links must use IPSec, MACsec, or Type 1 encryption to protect CUI in transit (NIST SC-8, SC-13).",
        "check": "wan_encryption",
    },
    {
        "id": "NET-ENC-002",
        "title": "Type 1 (NSA) encryption required for SECRET+",
        "severity": "CAT1",
        "category": "encryption",
        "regimes": ["icd503", "cnss1253"],
        "description": "SECRET and above require NSA Type 1 encryption (KG-175D, KG-250, etc.) per CNSS Policy 15.",
        "check": "type1_encryption",
    },
    {
        "id": "NET-ENC-003",
        "title": "Encryptor speed rating matches link bandwidth",
        "severity": "CAT2",
        "category": "encryption",
        "regimes": ["fisma_high", "stig", "fips", "cnss1253"],
        "description": "Encryption device throughput must meet or exceed link bandwidth (e.g., KG-175D ≤10G, KG-250 ≤100G).",
        "check": "encryptor_speed_match",
    },
    {
        "id": "NET-ENC-004",
        "title": "FIPS 140-2/3 validated crypto on all encrypted links",
        "severity": "CAT1",
        "category": "encryption",
        "regimes": ["fisma_high", "fips", "cjis", "icd503"],
        "description": "All cryptographic modules must be FIPS 140-2 Level 2+ validated (NIST SC-13).",
        "check": "fips_validated_crypto",
    },
    # ── Redundancy ──────────────────────────────────────────────────────────
    {
        "id": "NET-RED-001",
        "title": "Core/distribution devices require dual uplinks",
        "severity": "CAT1",
        "category": "redundancy",
        "regimes": ["fisma_high", "stig", "cjis", "cnss1253"],
        "description": "Core and distribution switches/routers must have ≥2 uplinks to prevent single point of failure (NIST CP-8, SC-36).",
        "check": "core_dual_uplinks",
    },
    {
        "id": "NET-RED-002",
        "title": "Diverse path routing for critical circuits",
        "severity": "CAT2",
        "category": "redundancy",
        "regimes": ["fisma_high", "stig", "cnss1253"],
        "description": "Critical circuits should traverse physically diverse paths (different conduit/provider) per NIST CP-8.",
        "check": "diverse_paths",
    },
    {
        "id": "NET-RED-003",
        "title": "Access layer single uplink acceptable with documentation",
        "severity": "CAT3",
        "category": "redundancy",
        "regimes": ["fisma_high", "stig"],
        "description": "Access-layer switches with single uplink are acceptable if documented in the SSP with risk acceptance.",
        "check": "access_single_uplink_documented",
    },
    # ── Boundary / Firewall ──────────────────────────────────────────────────
    {
        "id": "NET-BND-001",
        "title": "Firewall between internal and WAN/internet",
        "severity": "CAT1",
        "category": "boundary",
        "regimes": ["fisma_high", "stig", "zta", "cjis", "icd503", "cnss1253"],
        "description": "Every site must have a firewall between internal networks and WAN/internet segments (NIST SC-7).",
        "check": "firewall_at_boundary",
    },
    {
        "id": "NET-BND-002",
        "title": "Micro-segmentation between security zones",
        "severity": "CAT2",
        "category": "boundary",
        "regimes": ["zta", "fisma_high", "icd503"],
        "description": "Zero Trust requires network segmentation between security zones — no flat networks (NIST 800-207 §3.1).",
        "check": "micro_segmentation",
    },
    {
        "id": "NET-BND-003",
        "title": "Cloud VPC/VNet isolation from on-prem",
        "severity": "CAT2",
        "category": "boundary",
        "regimes": ["fisma_high", "stig", "zta", "cjis"],
        "description": "Cloud environments must be isolated from on-prem via dedicated interconnect with firewall/ACL (NIST SC-7).",
        "check": "cloud_isolation",
    },
    # ── Management Plane ──────────────────────────────────────────────────────
    {
        "id": "NET-MGT-001",
        "title": "Out-of-band management network",
        "severity": "CAT2",
        "category": "management",
        "regimes": ["fisma_high", "stig", "icd503", "cnss1253"],
        "description": "Network devices should be managed via out-of-band (OOB) management network, separate from production traffic (NIST SC-7(13)).",
        "check": "oob_management",
    },
    {
        "id": "NET-MGT-002",
        "title": "In-band management encrypted (SSH/HTTPS only)",
        "severity": "CAT2",
        "category": "management",
        "regimes": ["fisma_high", "stig", "cjis", "zta"],
        "description": "If in-band management is used, it must be encrypted (SSH, HTTPS) — no Telnet, HTTP, SNMPv1/v2 (NIST SC-8).",
        "check": "inband_encrypted",
    },
    # ── DNS ──────────────────────────────────────────────────────────────────
    {
        "id": "NET-DNS-001",
        "title": "DNS redundancy (≥2 DNS servers)",
        "severity": "CAT2",
        "category": "dns",
        "regimes": ["fisma_high", "stig", "cjis"],
        "description": "At least 2 DNS resolvers or cloud DNS service for fault tolerance (NIST SC-20, SC-22).",
        "check": "dns_redundancy",
    },
    # ── Zero Trust Specific ──────────────────────────────────────────────────
    {
        "id": "NET-ZTA-001",
        "title": "No implicit trust zones",
        "severity": "CAT1",
        "category": "zta",
        "regimes": ["zta"],
        "description": "Zero Trust architecture requires all access to be explicitly verified — no flat trust zones (NIST 800-207 §2.1).",
        "check": "no_implicit_trust",
    },
    {
        "id": "NET-ZTA-002",
        "title": "East-west traffic inspection",
        "severity": "CAT2",
        "category": "zta",
        "regimes": ["zta"],
        "description": "Traffic between internal segments must be inspected (firewall or IDS/IPS between zones) per NIST 800-207.",
        "check": "east_west_inspection",
    },
    # ── CJIS Specific ──────────────────────────────────────────────────────
    {
        "id": "NET-CJIS-001",
        "title": "128-bit encryption minimum for CJI",
        "severity": "CAT1",
        "category": "encryption",
        "regimes": ["cjis"],
        "description": "CJIS Security Policy 5.10.1.2 requires minimum 128-bit encryption (AES) for Criminal Justice Information in transit.",
        "check": "cjis_128bit",
    },
    # ── General Best Practice ──────────────────────────────────────────────
    {
        "id": "NET-BP-001",
        "title": "All devices labeled with hostname",
        "severity": "CAT3",
        "category": "documentation",
        "regimes": ["fisma_high", "stig", "cjis", "icd503", "cnss1253"],
        "description": "All network devices should have meaningful hostnames for identification in audit logs.",
        "check": "devices_labeled",
    },
    {
        "id": "NET-BP-002",
        "title": "Network diagram matches as-built documentation",
        "severity": "CAT3",
        "category": "documentation",
        "regimes": ["fisma_high", "stig", "icd503"],
        "description": "Topology diagram should have a saved version labeled 'as-built' for ATO documentation.",
        "check": "as_built_version",
    },
]

# Encryptor speed ratings (Mbps) for NET-ENC-003
ENCRYPTOR_RATINGS = {
    "kg-175d": 10000,
    "kg-175g": 10000,
    "kg-250": 100000,
    "kg-340": 400000,
    "kg-245x": 10000,
    "kg-255": 100000,
    "fips-140-l1": 10000,
    "fips-140-l2": 10000,
    "fips-140-l3": 100000,
    "fips-140-l4": 100000,
    "macsec": 400000,
    "type1-encryptor": 10000,
    "hsm": 1000,
}

# Bill of Materials unit costs (USD) per device type
BOM_COSTS = {
    "router": 15000,
    "switch-l2": 3000,
    "switch-l3": 8000,
    "firewall": 25000,
    "load-balancer": 20000,
    # Cisco-branded physical devices
    "cisco-router": 15000,
    "cisco-switch-l2": 3000,
    "cisco-switch-l3": 8000,
    "cisco-firewall": 25000,
    "cisco-lb": 20000,
    # Juniper-branded physical devices
    "juniper-ptx10003": 150000,
    "juniper-mx304": 45000,
    "wap": 800,
    "server": 5000,
    "patch-panel": 200,
    "roadm": 45000,
    "oadm": 12000,
    "edfa": 8000,
    "transponder": 18000,
    "olt": 10000,
    "odf": 500,
    "sonet-adm": 35000,
    "media-ge": 50,
    "media-10ge": 150,
    "media-25ge": 300,
    "media-40ge": 500,
    "media-100ge": 1200,
    "media-400ge": 3500,
    "media-fiber": 80,
    "media-optical": 200,
    "sfp": 80,
    "sfp-plus": 200,
    "qsfp": 800,
    "qsfp-dd": 2000,
    "patch-panel-fiber": 400,
    # Colocation facility objects
    "meet-me-room": 0,
    "cross-connect": 300,
    "demarc": 0,
    "cage": 0,
    "cabinet": 2500,
    # Cloud objects are OpEx, not CapEx — zero hardware cost
}

# ── SCCA (Secure Cloud Computing Architecture) ────────────────────────────────
# DoD DISA SCCA defines 4 functional components mandatory for any DoD cloud
# deployment connecting to DISN. References:
#   - DISA SCCA Functional Requirements Document (FRD) v2.9
#   - AWS Prescriptive Guidance: Secure Architecture for DoD
#   - Azure SACA (Secure Azure Computing Architecture)
#   - OCI SCCA Landing Zone Reference Architecture

SCCA_COMPONENTS = {
    "bcap": {
        "name": "Boundary Cloud Access Point",
        "acronym": "BCAP",
        "description": "Protects DISN from cloud-originating attacks. Provides network boundary "
        "security between DoD networks and commercial cloud. Typically operated "
        "by DISA using Cloud Native Access Point (CNAP) reference design.",
        "disa_ref": "FRD §2.1.1",
        "operator": "DISA or DoD Component",
    },
    "vdss": {
        "name": "Virtual Datacenter Security Stack",
        "acronym": "VDSS",
        "description": "Bulk security operations for DoD mission-owner applications in cloud. "
        "Provides inbound access controls, perimeter protections, WAF, DDoS, "
        "IDS/IPS, SSL/TLS inspection, and PPSM enforcement.",
        "disa_ref": "FRD §2.1.2",
        "requirements": [
            {"id": "2.1.2.1", "title": "Virtual separation of management/user/data traffic"},
            {"id": "2.1.2.2", "title": "Encryption for segmentation of management traffic"},
            {"id": "2.1.2.3", "title": "Reverse proxy for access requests"},
            {"id": "2.1.2.4", "title": "Application-layer inspection/filtering (HTTP)"},
            {"id": "2.1.2.5", "title": "Block unauthorized application-layer traffic"},
            {"id": "2.1.2.6", "title": "IDS — monitor/detect/report malicious activities"},
            {"id": "2.1.2.7", "title": "IPS — stop/block detected malicious activity"},
            {"id": "2.1.2.8", "title": "Inspect/filter traffic between mission VPCs"},
            {"id": "2.1.2.9", "title": "SSL/TLS break and inspection"},
            {"id": "2.1.2.10", "title": "Ports/Protocols/Services Management (PPSM)"},
            {"id": "2.1.2.11", "title": "Monitoring — log files and event data"},
            {"id": "2.1.2.12", "title": "SIEM — feed security data to archiving system"},
            {"id": "2.1.2.13", "title": "FIPS 140-2 encryption key management for WAF SSL/TLS"},
            {"id": "2.1.2.14", "title": "Detect application session hijacking"},
            {"id": "2.1.2.15", "title": "DoD DMZ extension for internet-facing applications"},
            {"id": "2.1.2.16", "title": "Full packet capture capability"},
            {"id": "2.1.2.17", "title": "Network packet flow metrics/statistics"},
            {"id": "2.1.2.18", "title": "Inspect traffic entering/exiting each mission VPC"},
        ],
    },
    "vdms": {
        "name": "Virtual Datacenter Managed Services",
        "acronym": "VDMS",
        "description": "Host security and shared datacenter services. Provides ACAS continuous "
        "monitoring, HBSS endpoint security, identity services (CAC), patch "
        "management, directory services, and centralized logging.",
        "disa_ref": "FRD §2.1.3",
        "requirements": [
            {"id": "2.1.3.1", "title": "ACAS continuous monitoring"},
            {"id": "2.1.3.2", "title": "HBSS endpoint security"},
            {"id": "2.1.3.3", "title": "Identity services — CAC two-factor auth"},
            {"id": "2.1.3.4", "title": "Configuration and patch management"},
            {"id": "2.1.3.5", "title": "Directory, federation, DHCP, DNS"},
            {"id": "2.1.3.6", "title": "Separate management network"},
            {"id": "2.1.3.7", "title": "System/security/app event logging and archiving"},
            {"id": "2.1.3.8", "title": "Exchange privileged user auth attributes with CSP IAM"},
            {"id": "2.1.3.9", "title": "Implement TCCM technical capabilities"},
        ],
    },
    "tccm": {
        "name": "Trusted Cloud Credential Manager",
        "acronym": "TCCM",
        "description": "Credential management, RBAC enforcement, and least-privilege access. "
        "Appointed by Authorizing Official (AO). Develops Cloud Credential "
        "Management Plan (CCMP) and validates before DISN connection.",
        "disa_ref": "FRD §2.1.4",
        "requirements": [
            {"id": "2.1.4.1", "title": "Develop Cloud Credential Management Plan (CCMP)"},
            {"id": "2.1.4.2", "title": "Collect/audit/archive customer portal activity logs"},
            {"id": "2.1.4.3", "title": "Forward activity log alerts to DoD privileged users"},
            {"id": "2.1.4.4", "title": "Create log repository access accounts for BCP/MCP"},
            {"id": "2.1.4.5", "title": "Recover/control portal credentials before DISN connectivity"},
            {"id": "2.1.4.6", "title": "Create/issue/revoke RBAC least-privileged portal credentials"},
        ],
    },
}

# ── SCCA Component-to-CSP Service Mapping ──────────────────────────────────────
# Maps each SCCA functional component to native services across all 5 CSPs.
# Used by cloud_architecture.py for SCCA compliance analysis and by NDC
# templates for generating CSP-specific SCCA topologies.

SCCA_CSP_MAPPING = {
    "bcap": {
        "description": "Boundary protection — DISN-to-cloud connectivity with IDS/IPS",
        "aws": {
            "services": ["Direct Connect", "Transit Gateway", "CloudFront", "Shield", "WAF"],
            "node_types": ["aws-dx", "aws-tgw", "aws-cloudfront", "aws-shield", "aws-waf"],
            "note": "DISA operates BCAP; customer uses DX/TGW to connect",
        },
        "azure": {
            "services": ["ExpressRoute", "Azure Front Door", "DDoS Protection"],
            "node_types": ["az-er", "az-front", "az-ddos"],
            "note": "DISA Gen 2/3 BCAPs with ExpressRoute circuits",
        },
        "gcp": {
            "services": ["Dedicated Interconnect", "Cloud Armor"],
            "node_types": ["gcp-ic", "gcp-armor"],
            "note": "MACsec or VPN-over-Interconnect encryption",
        },
        "oci": {
            "services": ["FastConnect", "Load Balancer", "WAF"],
            "node_types": ["oci-fc", "oci-lb", "oci-waf"],
            "note": "CAP mapped to LB + WAF at boundary",
        },
        "ibm": {
            "services": ["Direct Link", "Transit Gateway", "CIS"],
            "node_types": ["ibm-dl", "ibm-tg", "ibm-cis"],
            "note": "FedRAMP High authorized; BCAP integration via Direct Link",
        },
    },
    "vdss": {
        "description": "Security stack — firewall, IDS/IPS, WAF, DDoS, traffic inspection",
        "aws": {
            "services": ["Network Firewall", "WAF", "Shield Advanced", "GuardDuty", "VPC Flow Logs", "Gateway LB"],
            "node_types": ["aws-nfw", "aws-waf", "aws-shield", "aws-guardduty", "aws-flowlogs", "aws-gwlb"],
            "note": "Network Firewall in centralized inspection VPC behind TGW",
        },
        "azure": {
            "services": ["Azure Firewall", "App Gateway WAF", "Front Door WAF", "DDoS Protection", "Network Watcher"],
            "node_types": ["az-fw", "az-appgw", "az-front", "az-ddos", "az-netwatcher"],
            "note": "Azure FW or NVAs (Palo Alto/F5/Citrix) in hub VNet",
        },
        "gcp": {
            "services": ["VPC Firewall Rules", "Cloud Armor", "Packet Mirroring"],
            "node_types": ["gcp-armor", "gcp-flowlogs"],
            "note": "No centralized inline firewall; Cloud Armor at regional LB",
        },
        "oci": {
            "services": ["Network Firewall", "WAF", "DDoS Protection"],
            "node_types": ["oci-nfw", "oci-waf", "oci-ddos"],
            "note": "OCI Network FW (Palo Alto PaaS) in VDSS VCN hub",
        },
        "ibm": {
            "services": ["VPC Security Groups", "Network ACLs", "CIS WAF/DDoS"],
            "node_types": ["ibm-cis"],
            "note": "Fortigate NVAs for inline inspection; CIS for edge",
        },
    },
    "vdms": {
        "description": "Managed services — vulnerability scanning, patching, logging, identity",
        "aws": {
            "services": [
                "Inspector",
                "Security Hub",
                "Config",
                "Systems Manager",
                "CloudTrail",
                "CloudWatch Logs",
                "Managed AD",
                "KMS",
                "Secrets Manager",
                "Private CA",
            ],
            "node_types": [
                "aws-inspector",
                "aws-securityhub",
                "aws-config",
                "aws-ssm",
                "aws-ct",
                "aws-ad",
                "aws-kms",
                "aws-privateca",
            ],
            "note": "ACAS via Inspector+SecurityHub; HBSS via third-party on EC2",
        },
        "azure": {
            "services": ["Defender for Cloud", "Sentinel", "Monitor", "Key Vault", "Entra ID", "Update Management"],
            "node_types": ["az-defender", "az-sentinel", "az-monitor", "az-keyvault", "az-entra"],
            "note": "Defender for CSPM + workload protection; Sentinel for SIEM",
        },
        "gcp": {
            "services": ["Security Command Center", "Cloud KMS", "Cloud Logging", "Cloud Audit Logs", "OS Config"],
            "node_types": ["gcp-scc", "gcp-kms"],
            "note": "SCC for centralized findings; limited HBSS integration",
        },
        "oci": {
            "services": [
                "Cloud Guard",
                "Vault",
                "Vulnerability Scanning",
                "Identity Domains",
                "Audit",
                "Logging Analytics",
            ],
            "node_types": ["oci-cloudguard", "oci-vault", "oci-vss", "oci-identity", "oci-audit"],
            "note": "Cloud Guard responder recipes for auto-remediation",
        },
        "ibm": {
            "services": [
                "Security & Compliance Center",
                "Key Protect",
                "Hyper Protect Crypto",
                "Log Analysis",
                "Activity Tracker",
            ],
            "node_types": ["ibm-scc", "ibm-keyprotect", "ibm-hpcs"],
            "note": "SCC profiles for posture monitoring; HPCS is FIPS 140-2 L4",
        },
    },
    "tccm": {
        "description": "Credential management — IAM, SSO, RBAC, MFA, audit trail",
        "aws": {
            "services": ["IAM", "IAM Identity Center", "Managed AD", "CloudTrail"],
            "node_types": ["aws-idc", "aws-ad", "aws-ct"],
            "note": "IAM Identity Center for SSO/RBAC across org accounts",
        },
        "azure": {
            "services": ["Entra ID", "RBAC", "Conditional Access", "Monitor"],
            "node_types": ["az-entra", "az-monitor"],
            "note": "Entra ID with CAC/PIV certificate-based auth",
        },
        "gcp": {
            "services": ["Cloud IAM", "Workforce Identity Federation", "Organization Policy", "Cloud Audit Logs"],
            "node_types": ["gcp-orgpolicy"],
            "note": "Workforce Identity Federation for CAC via IdP",
        },
        "oci": {
            "services": ["IAM Policies", "Identity Domains", "Audit"],
            "node_types": ["oci-identity", "oci-audit"],
            "note": "CAC/PIV via X.509 certificates in Identity Domains",
        },
        "ibm": {
            "services": ["IBM Cloud IAM", "App ID", "Activity Tracker"],
            "node_types": ["ibm-appid"],
            "note": "SAML federation via App ID; Activity Tracker for audit",
        },
    },
}

# ── Landing Zone Patterns per CSP ──────────────────────────────────────────────
# Multi-account/subscription/compartment structures with SCCA-compliant network
# topology patterns. Used by NDC template generation and IaC generators.

LANDING_ZONE_PATTERNS = {
    "aws": {
        "name": "AWS Landing Zone Accelerator (LZA)",
        "network_pattern": "tgw_hub_spoke",
        "description": "CDK-based multi-account architecture via Control Tower + Organizations. "
        "Transit Gateway hub with Network Firewall inspection VPC. "
        "Config-driven: accounts-config.yaml, network-config.yaml, security-config.yaml.",
        "reference": "https://aws.amazon.com/solutions/implementations/landing-zone-accelerator-on-aws/",
        "account_structure": [
            {"name": "Management", "purpose": "Organizations root, Control Tower, billing"},
            {
                "name": "LogArchive",
                "ou": "Security",
                "purpose": "Immutable log aggregation — CloudTrail, VPC Flow Logs, Config",
            },
            {
                "name": "Audit",
                "ou": "Security",
                "purpose": "SecurityHub aggregator, GuardDuty delegated admin, Config aggregator",
            },
            {
                "name": "Network",
                "ou": "Infrastructure",
                "purpose": "Transit Gateway, Network Firewall, NAT, VPC Endpoints, DX/VPN",
            },
            {
                "name": "SharedServices",
                "ou": "Infrastructure",
                "purpose": "Managed AD, Secrets Manager, Private CA, SNS",
            },
            {"name": "VDSS", "ou": "Workloads", "purpose": "Boundary security — WAF, Shield, IDS/IPS rules"},
            {"name": "VDMS", "ou": "Workloads", "purpose": "HBSS, ACAS, patching, authentication"},
            {"name": "MissionApp", "ou": "Workloads", "purpose": "Core workloads — multi-tier applications"},
        ],
        "il_tiers": {
            "IL4": {"region": "us-gov-west-1", "encryption": "AES-256 + KMS CMK", "isolation": "shared accounts OK"},
            "IL5": {
                "region": "us-gov-west-1",
                "encryption": "FIPS 140-2 L1 KMS",
                "isolation": "separate accounts required",
            },
            "IL6": {
                "region": "aws-secret",
                "encryption": "FIPS 140-2 L2 + CloudHSM",
                "isolation": "air-gapped, NSA Type 1",
            },
        },
    },
    "azure": {
        "name": "Azure Mission Landing Zone (MLZ)",
        "network_pattern": "hub_spoke_vnet",
        "description": "Hub-spoke VNet topology with Azure Firewall or NVAs in hub. "
        "Management groups for governance. MLZ Terraform or SCCA Enclave Starter.",
        "reference": "https://github.com/Azure/missionlz",
        "account_structure": [
            {"name": "Hub Subscription", "purpose": "VDSS+VDMS — Azure Firewall/NVA, Bastion, identity, monitoring"},
            {"name": "Identity Subscription", "purpose": "Entra ID, Conditional Access, CAC/PIV auth"},
            {"name": "Logging Subscription", "purpose": "Log Analytics workspace, Sentinel, long-term retention"},
            {"name": "Spoke Subscription(s)", "purpose": "Mission workloads — one per mission owner"},
        ],
        "il_tiers": {
            "IL4": {
                "region": "usgovvirginia",
                "encryption": "AES-256 + Key Vault",
                "isolation": "shared subscriptions OK",
            },
            "IL5": {
                "region": "usgovvirginia",
                "encryption": "FIPS 140-2 L2 Key Vault",
                "isolation": "dedicated infrastructure",
            },
            "IL6": {
                "region": "Azure Government Secret",
                "encryption": "FIPS 140-2 L3 + mHSM",
                "isolation": "air-gapped, physically isolated",
            },
        },
    },
    "gcp": {
        "name": "GCP Assured Workloads",
        "network_pattern": "shared_vpc",
        "description": "Software-defined community cloud via Assured Workloads folders. "
        "Organization policies enforce data residency and personnel controls. "
        "Shared VPC for centralized network governance.",
        "reference": "https://github.com/GCP-Architecture-Guides/csa-il4-assured-workload",
        "account_structure": [
            {"name": "Organization", "purpose": "Org-level policies, billing, IAM"},
            {"name": "Assured Workloads Folder", "purpose": "IL4/IL5 compliance controls via org policies"},
            {"name": "Host Project", "purpose": "Shared VPC host — centralized network, firewall rules"},
            {"name": "Service Project(s)", "purpose": "Workload projects using Shared VPC subnets"},
        ],
        "il_tiers": {
            "IL4": {
                "region": "us-central1/us-east4",
                "encryption": "Cloud KMS",
                "isolation": "Assured Workloads folder",
            },
            "IL5": {
                "region": "us-central1/us-east4",
                "encryption": "Cloud HSM (FIPS 140-2 L3)",
                "isolation": "Assured Workloads + EKM",
            },
            "IL6": {"region": "N/A", "encryption": "N/A", "isolation": "Not available — use Google Distributed Cloud"},
        },
    },
    "oci": {
        "name": "OCI SCCA Landing Zone",
        "network_pattern": "drg_hub_spoke",
        "description": "Turnkey SCCA deployment via Terraform. VDSS/VDMS/Workload compartments "
        "with DRG hub. Two variants: SCCAv1 (self-deploy) and SCCAv2 (managed broker). "
        "Includes Network Firewall, Cloud Guard, Vault, and identity isolation.",
        "reference": "https://github.com/oci-landing-zones/oci-scca-landingzone",
        "account_structure": [
            {"name": "VDSS Compartment", "purpose": "Network Firewall, WAF, DDoS, boundary security"},
            {"name": "VDMS Compartment", "purpose": "Cloud Guard, Vault, VSS, Identity Domains, logging"},
            {"name": "Workload Compartment(s)", "purpose": "Mission VCNs — one per mission owner"},
            {"name": "Logging Compartment", "purpose": "OCI Audit + Logging Analytics aggregation"},
        ],
        "il_tiers": {
            "IL4": {
                "region": "OC2/OC3 Government",
                "encryption": "AES-256 + OCI Vault",
                "isolation": "compartment isolation",
            },
            "IL5": {
                "region": "OC2/OC3 Government",
                "encryption": "Dedicated HSM Vault (~$11K/mo)",
                "isolation": "dedicated VCNs",
            },
            "IL6": {
                "region": "National Security regions",
                "encryption": "Classified HSM",
                "isolation": "air-gapped classified environment",
            },
        },
    },
    "ibm": {
        "name": "IBM Cloud VPC Landing Zone",
        "network_pattern": "transit_gw",
        "description": "Management + Workload VPCs connected via Transit Gateway. "
        "Security & Compliance Center for posture monitoring. "
        "FedRAMP High authorized but no formal SCCA landing zone published.",
        "reference": "https://github.com/terraform-ibm-modules/terraform-ibm-landing-zone-vpc",
        "account_structure": [
            {"name": "Management VPC", "purpose": "Control plane — bastion, monitoring, security tools"},
            {"name": "Workload VPC", "purpose": "Application workloads — compute, storage, databases"},
            {"name": "Edge VPC", "purpose": "Optional — VPN/Direct Link termination, edge security"},
        ],
        "il_tiers": {
            "IL4": {
                "region": "us-south/us-east",
                "encryption": "Key Protect + BYOK",
                "isolation": "resource group isolation",
            },
            "IL5": {
                "region": "N/A",
                "encryption": "HPCS (FIPS 140-2 L4)",
                "isolation": "Not formally documented for IL5",
            },
            "IL6": {"region": "N/A", "encryption": "N/A", "isolation": "Not available"},
        },
    },
}

# ── SCCA Compliance Rules for NDC Audit ────────────────────────────────────────
# Checked by compliance.py when SCCA regime is selected. Each rule maps to a
# specific SCCA FRD requirement and checks the topology for required components.

SCCA_COMPLIANCE_RULES = [
    # ── BCAP ──
    {
        "id": "SCCA-BCAP-001",
        "title": "Dedicated interconnect to DISN/BCAP",
        "severity": "CAT1",
        "category": "bcap",
        "regimes": ["scca"],
        "description": "Topology must include a dedicated circuit (DX/ER/IC/FC/DL) connecting to DISA BCAP or DoD network (FRD §2.1.1).",
        "check": "has_dedicated_interconnect",
    },
    # ── VDSS ──
    {
        "id": "SCCA-VDSS-001",
        "title": "Network firewall for traffic inspection",
        "severity": "CAT1",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "VDSS requires centralized firewall for IDS/IPS, SSL/TLS inspection, and PPSM enforcement (FRD §2.1.2.7-2.1.2.10).",
        "check": "has_network_firewall",
    },
    {
        "id": "SCCA-VDSS-002",
        "title": "WAF for application-layer filtering",
        "severity": "CAT2",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "VDSS requires WAF for HTTP inspection and blocking unauthorized application traffic (FRD §2.1.2.4-2.1.2.5).",
        "check": "has_waf",
    },
    {
        "id": "SCCA-VDSS-003",
        "title": "DDoS protection",
        "severity": "CAT2",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "VDSS requires DDoS protection at the perimeter (FRD §2.1.2.1).",
        "check": "has_ddos_protection",
    },
    {
        "id": "SCCA-VDSS-004",
        "title": "VPC Flow Logs enabled",
        "severity": "CAT2",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "Full packet flow metrics and statistics required for all VPCs (FRD §2.1.2.16-2.1.2.17).",
        "check": "has_flow_logs",
    },
    {
        "id": "SCCA-VDSS-005",
        "title": "East-west traffic inspection between mission VPCs",
        "severity": "CAT1",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "All inter-VPC traffic must transit through VDSS firewall for inspection (FRD §2.1.2.8, 2.1.2.18).",
        "check": "east_west_through_firewall",
    },
    {
        "id": "SCCA-VDSS-006",
        "title": "Transit hub for centralized routing",
        "severity": "CAT2",
        "category": "vdss",
        "regimes": ["scca"],
        "description": "SCCA requires centralized routing via transit hub (TGW/vWAN/DRG) to enforce inspection (FRD §2.1.2.8).",
        "check": "has_transit_hub",
    },
    # ── VDMS ──
    {
        "id": "SCCA-VDMS-001",
        "title": "Centralized logging and SIEM",
        "severity": "CAT1",
        "category": "vdms",
        "regimes": ["scca"],
        "description": "VDMS requires centralized security event logging and archiving to a SIEM system (FRD §2.1.3.7).",
        "check": "has_centralized_logging",
    },
    {
        "id": "SCCA-VDMS-002",
        "title": "Key management service (FIPS 140-2)",
        "severity": "CAT1",
        "category": "vdms",
        "regimes": ["scca"],
        "description": "FIPS 140-2 validated encryption key management required for all data at rest and WAF SSL/TLS (FRD §2.1.2.13).",
        "check": "has_kms",
    },
    {
        "id": "SCCA-VDMS-003",
        "title": "Identity and directory service with CAC/MFA",
        "severity": "CAT1",
        "category": "vdms",
        "regimes": ["scca"],
        "description": "VDMS requires identity services with CAC two-factor authentication (FRD §2.1.3.3).",
        "check": "has_identity_service",
    },
    {
        "id": "SCCA-VDMS-004",
        "title": "Vulnerability scanning (ACAS equivalent)",
        "severity": "CAT2",
        "category": "vdms",
        "regimes": ["scca"],
        "description": "Continuous monitoring via vulnerability scanning equivalent to ACAS (FRD §2.1.3.1).",
        "check": "has_vuln_scanning",
    },
    {
        "id": "SCCA-VDMS-005",
        "title": "Patch management service",
        "severity": "CAT2",
        "category": "vdms",
        "regimes": ["scca"],
        "description": "Configuration and patch management required across all workloads (FRD §2.1.3.4).",
        "check": "has_patch_management",
    },
    # ── TCCM ──
    {
        "id": "SCCA-TCCM-001",
        "title": "Centralized IAM with SSO",
        "severity": "CAT1",
        "category": "tccm",
        "regimes": ["scca"],
        "description": "TCCM requires centralized IAM with SSO and RBAC least-privileged credentials (FRD §2.1.4.6).",
        "check": "has_centralized_iam",
    },
    {
        "id": "SCCA-TCCM-002",
        "title": "API audit trail (CloudTrail/Audit equivalent)",
        "severity": "CAT1",
        "category": "tccm",
        "regimes": ["scca"],
        "description": "All portal/API activity must be logged, audited, and archived (FRD §2.1.4.2).",
        "check": "has_api_audit_trail",
    },
]

# ── SCCA Node Type Detection Sets ─────────────────────────────────────────────
# Used by cloud_architecture.py to detect SCCA components in a topology.

SCCA_FIREWALL_TYPES = {
    "firewall",
    "cisco-firewall",
    "aws-nfw",
    "az-fw",
    "oci-nfw",
    "gcp-armor",
    "aws-waf",
    "az-appgw",
    "az-front",
}

SCCA_IDENTITY_TYPES = {
    "aws-ad",
    "aws-idc",
    "aws-privateca",
    "az-entra",
    "oci-identity",
    "gcp-orgpolicy",
    "ibm-appid",
}

SCCA_LOGGING_TYPES = {
    "aws-ct",
    "aws-securityhub",
    "aws-config",
    "aws-guardduty",
    "az-sentinel",
    "az-defender",
    "az-monitor",
    "gcp-scc",
    "oci-audit",
    "oci-cloudguard",
    "ibm-scc",
}

SCCA_KMS_TYPES = {
    "aws-kms",
    "az-keyvault",
    "gcp-kms",
    "oci-vault",
    "ibm-keyprotect",
    "ibm-hpcs",
}

SCCA_SCANNING_TYPES = {
    "aws-inspector",
    "aws-ssm",
    "az-defender",
    "gcp-scc",
    "oci-vss",
    "oci-cloudguard",
    "ibm-scc",
}

SCCA_DDOS_TYPES = {
    "aws-shield",
    "az-ddos",
    "oci-ddos",
    "gcp-armor",
    "ibm-cis",
}

# ── AWS Well-Architected Security Pillar ───────────────────────────────────────
# 7 areas, 57 best practices (SEC01-SEC11). Risk levels from the official
# Security Pillar whitepaper (November 2024). Used by cloud_architecture.py
# for WA Security posture analysis and by compliance.py for audit rules.
# Reference: https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/

WA_SECURITY_AREAS = {
    "foundations": {
        "name": "Security Foundations",
        "code": "SEC01",
        "description": "Operating your workload securely — multi-account strategy, "
        "threat modeling, control objectives, and automated guardrails.",
    },
    "identity": {
        "name": "Identity and Access Management (Identity)",
        "code": "SEC02",
        "description": "Strong sign-in mechanisms, temporary credentials, "
        "centralized identity provider, secret management.",
    },
    "permissions": {
        "name": "Identity and Access Management (Permissions)",
        "code": "SEC03",
        "description": "Least privilege access, permission guardrails, lifecycle management, cross-account analysis.",
    },
    "detection": {
        "name": "Detection",
        "code": "SEC04",
        "description": "Service and application logging, centralized findings, correlation, and automated remediation.",
    },
    "network": {
        "name": "Infrastructure Protection (Networks)",
        "code": "SEC05",
        "description": "Network layers, traffic flow control, inspection-based "
        "protection, and automated network controls.",
    },
    "compute": {
        "name": "Infrastructure Protection (Compute)",
        "code": "SEC06",
        "description": "Vulnerability management, hardened images, reduced manual "
        "access, software integrity, and compute automation.",
    },
    "data_classification": {
        "name": "Data Protection (Classification)",
        "code": "SEC07",
        "description": "Data classification scheme, sensitivity-based controls, "
        "automated identification, and lifecycle management.",
    },
    "data_at_rest": {
        "name": "Data Protection (At Rest)",
        "code": "SEC08",
        "description": "Key management (FIPS 140-3 L3), enforced encryption, "
        "automated data-at-rest protection, access control.",
    },
    "data_in_transit": {
        "name": "Data Protection (In Transit)",
        "code": "SEC09",
        "description": "Key and certificate management, enforced TLS 1.2+, "
        "authenticated network communications (mTLS, IPsec).",
    },
    "incident_response": {
        "name": "Incident Response",
        "code": "SEC10",
        "description": "IR plans, forensic capabilities, playbooks, pre-provisioned "
        "access, simulations, and post-incident learning.",
    },
    "app_security": {
        "name": "Application Security",
        "code": "SEC11",
        "description": "Security training, automated SAST/DAST, pen testing, "
        "code reviews, dependency management, DevSecOps culture.",
    },
}

WA_SECURITY_BEST_PRACTICES = [
    # ── SEC01: Security Foundations ──────────────────────────────────────────
    {
        "id": "SEC01-BP01",
        "area": "foundations",
        "title": "Separate workloads using accounts",
        "risk": "high",
        "description": "Use AWS Organizations/Control Tower multi-account strategy; isolate production, dev, test in separate accounts.",
    },
    {
        "id": "SEC01-BP02",
        "area": "foundations",
        "title": "Secure account root user and properties",
        "risk": "high",
        "description": "Disable root programmatic access, enable MFA, do not use root for daily tasks.",
    },
    {
        "id": "SEC01-BP03",
        "area": "foundations",
        "title": "Identify and validate control objectives",
        "risk": "high",
        "description": "Define control objectives from compliance requirements (NIST 800-53, SOC2, PCI-DSS, ISO 27001).",
    },
    {
        "id": "SEC01-BP04",
        "area": "foundations",
        "title": "Stay up to date with security threats",
        "risk": "high",
        "description": "Subscribe to threat intelligence (MITRE ATT&CK, CVEs, OWASP Top 10); use GuardDuty/Inspector auto-updated feeds.",
    },
    {
        "id": "SEC01-BP05",
        "area": "foundations",
        "title": "Reduce security management scope",
        "risk": "medium",
        "description": "Use managed services (RDS, EKS, Lambda) to shift security responsibility to CSP.",
    },
    {
        "id": "SEC01-BP06",
        "area": "foundations",
        "title": "Automate deployment of standard security controls",
        "risk": "medium",
        "description": "Use IaC (CloudFormation/Terraform), CI/CD pipelines, Service Catalog; version-control security configs.",
    },
    {
        "id": "SEC01-BP07",
        "area": "foundations",
        "title": "Identify threats using a threat model",
        "risk": "high",
        "description": "Use STRIDE model; maintain living threat model; use AWS Threat Composer tool.",
    },
    {
        "id": "SEC01-BP08",
        "area": "foundations",
        "title": "Evaluate new security services regularly",
        "risk": "low",
        "description": "Subscribe to AWS security blogs/RSS, attend re:Inforce, consult TAM.",
    },
    # ── SEC02: Identity Management ──────────────────────────────────────────
    {
        "id": "SEC02-BP01",
        "area": "identity",
        "title": "Use strong sign-in mechanisms",
        "risk": "high",
        "description": "Require MFA for all human identities; enforce strong password policies per NIST 800-63.",
    },
    {
        "id": "SEC02-BP02",
        "area": "identity",
        "title": "Use temporary credentials",
        "risk": "high",
        "description": "Use IAM roles and STS for temp credentials; eliminate long-term access keys.",
    },
    {
        "id": "SEC02-BP03",
        "area": "identity",
        "title": "Store and use secrets securely",
        "risk": "high",
        "description": "Use Secrets Manager for secrets; auto-rotate; never hardcode credentials.",
    },
    {
        "id": "SEC02-BP04",
        "area": "identity",
        "title": "Rely on a centralized identity provider",
        "risk": "high",
        "description": "Use IAM Identity Center (SSO) with external IdP (SAML/OIDC); federate workforce access.",
    },
    {
        "id": "SEC02-BP05",
        "area": "identity",
        "title": "Audit and rotate credentials periodically",
        "risk": "high",
        "description": "Use IAM Access Analyzer to find unused credentials; auto-rotate via Secrets Manager.",
    },
    {
        "id": "SEC02-BP06",
        "area": "identity",
        "title": "Employ user groups and attributes",
        "risk": "high",
        "description": "Use IAM groups and ABAC (attribute-based access control) for scalable permissions.",
    },
    # ── SEC03: Permissions Management ───────────────────────────────────────
    {
        "id": "SEC03-BP01",
        "area": "permissions",
        "title": "Define access requirements",
        "risk": "high",
        "description": "Document who/what needs access to which resources under what conditions.",
    },
    {
        "id": "SEC03-BP02",
        "area": "permissions",
        "title": "Grant least privilege access",
        "risk": "high",
        "description": "Start with minimum permissions; use IAM Access Analyzer policy generation to right-size.",
    },
    {
        "id": "SEC03-BP03",
        "area": "permissions",
        "title": "Establish emergency access process",
        "risk": "high",
        "description": "Pre-provision break-glass access; document and test emergency access procedures.",
    },
    {
        "id": "SEC03-BP04",
        "area": "permissions",
        "title": "Reduce permissions continuously",
        "risk": "high",
        "description": "Use IAM Access Analyzer to identify unused permissions; remove regularly.",
    },
    {
        "id": "SEC03-BP05",
        "area": "permissions",
        "title": "Define permission guardrails for your organization",
        "risk": "high",
        "description": "Use SCPs and permission boundaries; define security invariants across organization.",
    },
    {
        "id": "SEC03-BP06",
        "area": "permissions",
        "title": "Manage access based on lifecycle",
        "risk": "high",
        "description": "Integrate IAM with HR systems; auto-revoke on role change/termination.",
    },
    {
        "id": "SEC03-BP07",
        "area": "permissions",
        "title": "Analyze public and cross-account access",
        "risk": "high",
        "description": "Use IAM Access Analyzer to detect public/cross-account resource sharing.",
    },
    {
        "id": "SEC03-BP08",
        "area": "permissions",
        "title": "Share resources securely within organization",
        "risk": "high",
        "description": "Use AWS Resource Access Manager (RAM) for controlled cross-account sharing.",
    },
    {
        "id": "SEC03-BP09",
        "area": "permissions",
        "title": "Share resources securely with third party",
        "risk": "high",
        "description": "Use IAM roles with external ID for third-party access; limit scope and duration.",
    },
    # ── SEC04: Detection ────────────────────────────────────────────────────
    {
        "id": "SEC04-BP01",
        "area": "detection",
        "title": "Configure service and application logging",
        "risk": "high",
        "description": "Enable CloudTrail, VPC Flow Logs, S3/ELB access logs; centralize in Security Lake.",
    },
    {
        "id": "SEC04-BP02",
        "area": "detection",
        "title": "Capture logs and findings in standardized locations",
        "risk": "high",
        "description": "Aggregate in central security account; use Security Hub, CloudWatch, S3 buckets.",
    },
    {
        "id": "SEC04-BP03",
        "area": "detection",
        "title": "Correlate and enrich security alerts",
        "risk": "high",
        "description": "Use Detective for investigation; Security Hub for aggregation; enrich with GuardDuty findings.",
    },
    {
        "id": "SEC04-BP04",
        "area": "detection",
        "title": "Initiate remediation for non-compliant resources",
        "risk": "high",
        "description": "Use AWS Config rules + remediation actions; EventBridge + Lambda for auto-remediation.",
    },
    # ── SEC05: Infrastructure Protection (Networks) ─────────────────────────
    {
        "id": "SEC05-BP01",
        "area": "network",
        "title": "Create network layers",
        "risk": "medium",
        "description": "Use public/private subnets, security groups, NACLs; isolate workload tiers.",
    },
    {
        "id": "SEC05-BP02",
        "area": "network",
        "title": "Control traffic flow within network layers",
        "risk": "high",
        "description": "Use security groups as primary control; restrict inter-layer traffic to required paths.",
    },
    {
        "id": "SEC05-BP03",
        "area": "network",
        "title": "Implement inspection-based protection",
        "risk": "high",
        "description": "Use Network Firewall, WAF for L3-L7 inspection; deploy intrusion detection.",
    },
    {
        "id": "SEC05-BP04",
        "area": "network",
        "title": "Automate network protection",
        "risk": "high",
        "description": "Use Firewall Manager for consistent policy; auto-deploy network controls via IaC.",
    },
    # ── SEC06: Infrastructure Protection (Compute) ──────────────────────────
    {
        "id": "SEC06-BP01",
        "area": "compute",
        "title": "Perform vulnerability management",
        "risk": "high",
        "description": "Use Inspector for continuous vulnerability scanning; Systems Manager for patching.",
    },
    {
        "id": "SEC06-BP02",
        "area": "compute",
        "title": "Provision compute from hardened images",
        "risk": "high",
        "description": "Use hardened AMIs; ECR for trusted container images; validate image signatures.",
    },
    {
        "id": "SEC06-BP03",
        "area": "compute",
        "title": "Reduce manual management and interactive access",
        "risk": "high",
        "description": "Use Systems Manager Session Manager instead of SSH/RDP; eliminate bastion hosts.",
    },
    {
        "id": "SEC06-BP04",
        "area": "compute",
        "title": "Validate software integrity",
        "risk": "high",
        "description": "Use AWS Signer for code signing; verify artifact integrity in CI/CD pipeline.",
    },
    {
        "id": "SEC06-BP05",
        "area": "compute",
        "title": "Automate compute protection",
        "risk": "high",
        "description": "Auto-replace non-compliant instances; use immutable infrastructure patterns.",
    },
    # ── SEC07: Data Protection (Classification) ─────────────────────────────
    {
        "id": "SEC07-BP01",
        "area": "data_classification",
        "title": "Understand your data classification scheme",
        "risk": "high",
        "description": "Define data sensitivity levels; tag resources with classification.",
    },
    {
        "id": "SEC07-BP02",
        "area": "data_classification",
        "title": "Apply data protection controls based on sensitivity",
        "risk": "high",
        "description": "Match encryption, access control, retention to classification level.",
    },
    {
        "id": "SEC07-BP03",
        "area": "data_classification",
        "title": "Automate identification and classification",
        "risk": "high",
        "description": "Use Macie for automated sensitive data discovery in S3; tag automatically.",
    },
    {
        "id": "SEC07-BP04",
        "area": "data_classification",
        "title": "Define scalable data lifecycle management",
        "risk": "medium",
        "description": "Automate retention, archival, deletion; use S3 lifecycle policies.",
    },
    # ── SEC08: Data Protection (At Rest) ────────────────────────────────────
    {
        "id": "SEC08-BP01",
        "area": "data_at_rest",
        "title": "Implement secure key management",
        "risk": "high",
        "description": "Use KMS (FIPS 140-3 L3 HSMs); CloudHSM for dedicated HSMs.",
    },
    {
        "id": "SEC08-BP02",
        "area": "data_at_rest",
        "title": "Enforce encryption at rest",
        "risk": "high",
        "description": "Enable default encryption on all storage (S3, EBS, RDS, DynamoDB).",
    },
    {
        "id": "SEC08-BP03",
        "area": "data_at_rest",
        "title": "Automate data at rest protection",
        "risk": "high",
        "description": "Use KMS key policies, S3 bucket policies, SCPs to enforce encryption.",
    },
    {
        "id": "SEC08-BP04",
        "area": "data_at_rest",
        "title": "Enforce access control",
        "risk": "low",
        "description": "Use IAM policies, S3 bucket policies, KMS key policies for fine-grained access.",
    },
    # ── SEC09: Data Protection (In Transit) ─────────────────────────────────
    {
        "id": "SEC09-BP01",
        "area": "data_in_transit",
        "title": "Implement secure key and certificate management",
        "risk": "high",
        "description": "Use Private CA or ACM for TLS certificates; automate renewal.",
    },
    {
        "id": "SEC09-BP02",
        "area": "data_in_transit",
        "title": "Enforce encryption in transit",
        "risk": "medium",
        "description": "Require TLS 1.2+ everywhere; use HTTPS endpoints; enforce via resource policies.",
    },
    {
        "id": "SEC09-BP03",
        "area": "data_in_transit",
        "title": "Authenticate network communications",
        "risk": "high",
        "description": "Use VPN/DX with IPsec; mTLS for service-to-service; VPC PrivateLink.",
    },
    # ── SEC10: Incident Response ────────────────────────────────────────────
    {
        "id": "SEC10-BP01",
        "area": "incident_response",
        "title": "Identify key personnel and external resources",
        "risk": "high",
        "description": "Document IR team; establish AWS Support/partner contacts.",
    },
    {
        "id": "SEC10-BP02",
        "area": "incident_response",
        "title": "Develop incident management plans",
        "risk": "medium",
        "description": "Create runbooks/playbooks; define escalation paths; align with NIST CSF.",
    },
    {
        "id": "SEC10-BP03",
        "area": "incident_response",
        "title": "Prepare forensic capabilities",
        "risk": "medium",
        "description": "Pre-configure forensic account; enable EBS snapshots, memory capture.",
    },
    {
        "id": "SEC10-BP04",
        "area": "incident_response",
        "title": "Develop and test IR playbooks",
        "risk": "high",
        "description": "Create playbooks for credential exposure, ransomware, DDoS; run tabletop exercises.",
    },
    {
        "id": "SEC10-BP05",
        "area": "incident_response",
        "title": "Pre-provision access",
        "risk": "high",
        "description": "Configure break-glass roles before incidents; use cross-account roles.",
    },
    {
        "id": "SEC10-BP06",
        "area": "incident_response",
        "title": "Pre-deploy tools",
        "risk": "medium",
        "description": "Deploy Detective, Security Hub, GuardDuty before incidents occur.",
    },
    {
        "id": "SEC10-BP07",
        "area": "incident_response",
        "title": "Run simulations",
        "risk": "medium",
        "description": "Conduct tabletop, purple team, and red team exercises.",
    },
    {
        "id": "SEC10-BP08",
        "area": "incident_response",
        "title": "Establish framework for learning from incidents",
        "risk": "medium",
        "description": "Conduct post-incident reviews; feed lessons into threat model and controls.",
    },
    # ── SEC11: Application Security ─────────────────────────────────────────
    {
        "id": "SEC11-BP01",
        "area": "app_security",
        "title": "Train for application security",
        "risk": "medium",
        "description": "Security training for developers; OWASP awareness; secure coding practices.",
    },
    {
        "id": "SEC11-BP02",
        "area": "app_security",
        "title": "Automate testing throughout development lifecycle",
        "risk": "medium",
        "description": "SAST/DAST in CI/CD pipeline; use CodeGuru Reviewer, Inspector for scanning.",
    },
    {
        "id": "SEC11-BP03",
        "area": "app_security",
        "title": "Perform regular penetration testing",
        "risk": "medium",
        "description": "Schedule pen tests; follow AWS penetration testing policy.",
    },
    {
        "id": "SEC11-BP04",
        "area": "app_security",
        "title": "Conduct code reviews",
        "risk": "medium",
        "description": "Mandatory security-focused code reviews; use CodeGuru Reviewer.",
    },
    {
        "id": "SEC11-BP05",
        "area": "app_security",
        "title": "Centralize services for packages and dependencies",
        "risk": "medium",
        "description": "Use CodeArtifact/ECR as private registries; scan dependencies for vulnerabilities.",
    },
    {
        "id": "SEC11-BP06",
        "area": "app_security",
        "title": "Deploy software programmatically",
        "risk": "medium",
        "description": "No manual deployments; use CodePipeline/CodeBuild; immutable deployments.",
    },
    {
        "id": "SEC11-BP07",
        "area": "app_security",
        "title": "Regularly assess security of pipelines",
        "risk": "medium",
        "description": "Audit CI/CD pipeline permissions; ensure pipeline integrity.",
    },
    {
        "id": "SEC11-BP08",
        "area": "app_security",
        "title": "Build program embedding security ownership in teams",
        "risk": "medium",
        "description": "Security champions in each team; shift-left security; DevSecOps culture.",
    },
]

# ── WA Security Pillar — CSP Service Mapping ───────────────────────────────────
# Maps each security area to native services across all 5 CSPs.

WA_SECURITY_CSP_MAPPING = {
    "foundations": {
        "aws": ["Organizations", "Control Tower", "IAM", "CloudFormation", "Service Catalog", "Artifact"],
        "azure": ["Management Groups", "Azure Landing Zones", "Entra ID", "Bicep/ARM", "Azure Policy"],
        "gcp": ["Organization Policy", "Assured Workloads", "Cloud IAM", "Deployment Manager"],
        "oci": ["Compartments", "Landing Zones", "IAM Policies", "Resource Manager"],
        "ibm": ["Enterprise Account", "IAM", "Schematics"],
    },
    "identity": {
        "aws": ["IAM", "IAM Identity Center", "STS", "Secrets Manager", "Directory Service", "Cognito"],
        "azure": ["Entra ID", "Key Vault (Secrets)", "Managed Identity", "Conditional Access"],
        "gcp": ["Cloud IAM", "Cloud Identity", "Secret Manager", "Workforce Identity Federation"],
        "oci": ["Identity Domains", "Vault (Secrets)", "Instance Principal"],
        "ibm": ["IAM", "App ID", "Secrets Manager"],
    },
    "permissions": {
        "aws": ["IAM Policies", "SCPs", "Permission Boundaries", "IAM Access Analyzer", "RAM"],
        "azure": ["Azure RBAC", "Azure Policy", "PIM", "Entra ID Governance"],
        "gcp": ["IAM Roles", "Organization Policies", "Policy Analyzer", "Recommender"],
        "oci": ["IAM Policies", "Compartment Policies", "Tag-Based Access"],
        "ibm": ["IAM Policies", "Resource Groups", "Access Groups"],
    },
    "detection": {
        "aws": ["GuardDuty", "Security Hub", "CloudTrail", "Config", "Detective", "Security Lake", "CloudWatch"],
        "azure": ["Defender for Cloud", "Sentinel", "Monitor", "Activity Log", "Policy"],
        "gcp": ["Security Command Center", "Cloud Audit Logs", "Cloud Monitoring", "Chronicle"],
        "oci": ["Cloud Guard", "Audit", "Logging Analytics", "Events"],
        "ibm": ["SCC", "Activity Tracker", "Log Analysis", "Monitoring"],
    },
    "network": {
        "aws": [
            "VPC",
            "Security Groups",
            "NACLs",
            "WAF",
            "Shield",
            "Network Firewall",
            "Firewall Manager",
            "PrivateLink",
        ],
        "azure": ["VNet", "NSG", "Azure Firewall", "WAF", "DDoS Protection", "Private Link", "Firewall Manager"],
        "gcp": ["VPC", "Firewall Rules", "Cloud Armor", "Private Service Connect", "Hierarchical Firewall"],
        "oci": ["VCN", "Security Lists", "NSGs", "Network Firewall", "WAF", "Service Gateway"],
        "ibm": ["VPC", "Security Groups", "ACLs", "CIS WAF/DDoS", "Virtual Private Endpoints"],
    },
    "compute": {
        "aws": ["Inspector", "Systems Manager", "EC2 Image Builder", "Signer", "ECR", "Nitro Enclaves"],
        "azure": ["Defender for Servers", "Update Management", "ACR", "Trusted Signing"],
        "gcp": ["Artifact Analysis", "Container Scanning", "OS Config", "Binary Authorization"],
        "oci": ["Vulnerability Scanning", "OS Management Hub", "Container Registry"],
        "ibm": ["SCC VA", "Container Registry", "Code Engine"],
    },
    "data_at_rest": {
        "aws": ["KMS", "CloudHSM", "S3 Encryption", "EBS Encryption", "RDS Encryption", "Backup"],
        "azure": ["Key Vault", "Managed HSM", "Storage Encryption", "Disk Encryption"],
        "gcp": ["Cloud KMS", "Cloud HSM", "CMEK", "Cloud Storage Encryption"],
        "oci": ["Vault", "Dedicated HSM", "Object Storage Encryption", "Block Volume Encryption"],
        "ibm": ["Key Protect", "HPCS", "Cloud Object Storage Encryption"],
    },
    "data_in_transit": {
        "aws": ["ACM", "Private CA", "CloudFront TLS", "ELB TLS", "PrivateLink", "VPN/DX IPsec"],
        "azure": ["App Service Certs", "Front Door TLS", "Private Link", "ExpressRoute IPsec"],
        "gcp": ["Certificate Manager", "CA Service", "Cloud CDN TLS", "Interconnect MACsec"],
        "oci": ["Certificates Service", "LB TLS", "FastConnect IPsec"],
        "ibm": ["Certificate Manager", "CIS TLS", "Direct Link IPsec"],
    },
    "incident_response": {
        "aws": ["Detective", "Security Hub", "GuardDuty", "Incident Manager", "EventBridge", "Lambda"],
        "azure": ["Sentinel", "Defender", "Logic Apps", "Event Grid"],
        "gcp": ["Chronicle SIEM", "SCC", "Eventarc", "Cloud Functions"],
        "oci": ["Cloud Guard", "Events", "Functions", "Notifications"],
        "ibm": ["QRadar", "SCC", "Event Notifications", "Code Engine"],
    },
    "app_security": {
        "aws": ["Inspector", "CodeGuru", "CodePipeline", "CodeBuild", "Signer", "ECR", "CodeArtifact"],
        "azure": ["DevOps", "Defender for DevOps", "ACR", "Trusted Signing"],
        "gcp": ["Cloud Build", "Artifact Registry", "Binary Authorization", "Container Analysis"],
        "oci": ["DevOps Service", "Container Registry", "Vulnerability Scanning"],
        "ibm": ["Tekton", "Container Registry", "Code Engine"],
    },
}

# ── WA Security Pillar Design Principles ───────────────────────────────────────

WA_SECURITY_DESIGN_PRINCIPLES = [
    "Implement a strong identity foundation",
    "Maintain traceability",
    "Apply security at all layers",
    "Automate security best practices",
    "Protect data in transit and at rest",
    "Keep people away from data",
    "Prepare for security events",
]

# ── WA Security Pillar — NDC Compliance Rules ─────────────────────────────────
# Checked by compliance.py when wa_security regime is selected.

WA_SECURITY_COMPLIANCE_RULES = [
    # ── Foundations ──
    {
        "id": "WA-SEC01-001",
        "title": "Multi-account / multi-VPC isolation",
        "severity": "CAT1",
        "category": "foundations",
        "regimes": ["wa_security"],
        "description": "Workloads should be separated into distinct VPCs/accounts for blast-radius containment (SEC01-BP01).",
        "check": "multi_vpc_isolation",
        "wa_ref": "SEC01-BP01",
    },
    {
        "id": "WA-SEC01-002",
        "title": "Automated security controls via IaC",
        "severity": "CAT2",
        "category": "foundations",
        "regimes": ["wa_security"],
        "description": "Security controls should be deployed via IaC, not manual configuration (SEC01-BP06).",
        "check": "iac_deployed",
        "wa_ref": "SEC01-BP06",
    },
    # ── Identity ──
    {
        "id": "WA-SEC02-001",
        "title": "Centralized identity provider",
        "severity": "CAT1",
        "category": "identity",
        "regimes": ["wa_security"],
        "description": "Topology should include a centralized identity service (IAM Identity Center, Entra ID, etc.) for SSO/federation (SEC02-BP04).",
        "check": "has_identity_service",
        "wa_ref": "SEC02-BP04",
    },
    # ── Detection ──
    {
        "id": "WA-SEC04-001",
        "title": "Centralized logging and monitoring",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["wa_security"],
        "description": "Topology must include centralized logging (CloudTrail, Sentinel, SCC) and monitoring services (SEC04-BP01).",
        "check": "has_centralized_logging",
        "wa_ref": "SEC04-BP01",
    },
    {
        "id": "WA-SEC04-002",
        "title": "Threat detection service enabled",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["wa_security"],
        "description": "GuardDuty/Defender/Cloud Guard should be present for automated threat detection (SEC04-BP03).",
        "check": "has_threat_detection",
        "wa_ref": "SEC04-BP03",
    },
    {
        "id": "WA-SEC04-003",
        "title": "Compliance monitoring (Config/Policy)",
        "severity": "CAT2",
        "category": "detection",
        "regimes": ["wa_security"],
        "description": "AWS Config or equivalent should monitor resource compliance continuously (SEC04-BP04).",
        "check": "has_config_monitoring",
        "wa_ref": "SEC04-BP04",
    },
    # ── Network ──
    {
        "id": "WA-SEC05-001",
        "title": "Network layer segmentation",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["wa_security"],
        "description": "Topology must have distinct network layers (public/private subnets) with security groups (SEC05-BP01).",
        "check": "has_network_layers",
        "wa_ref": "SEC05-BP01",
    },
    {
        "id": "WA-SEC05-002",
        "title": "Network traffic inspection (firewall/WAF)",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["wa_security"],
        "description": "Network Firewall or WAF should inspect traffic at L3-L7 (SEC05-BP03).",
        "check": "has_network_firewall",
        "wa_ref": "SEC05-BP03",
    },
    {
        "id": "WA-SEC05-003",
        "title": "DDoS protection at perimeter",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["wa_security"],
        "description": "Shield/DDoS Protection/Cloud Armor should protect internet-facing resources (SEC05-BP03).",
        "check": "has_ddos_protection",
        "wa_ref": "SEC05-BP03",
    },
    {
        "id": "WA-SEC05-004",
        "title": "VPC Flow Logs enabled",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["wa_security"],
        "description": "VPC/VNet flow logs should capture traffic metadata for analysis (SEC04-BP01).",
        "check": "has_flow_logs",
        "wa_ref": "SEC04-BP01",
    },
    # ── Data Protection ──
    {
        "id": "WA-SEC08-001",
        "title": "Key management service (FIPS 140-2/3)",
        "severity": "CAT1",
        "category": "data_at_rest",
        "regimes": ["wa_security"],
        "description": "FIPS-validated KMS must manage encryption keys for all data at rest (SEC08-BP01).",
        "check": "has_kms",
        "wa_ref": "SEC08-BP01",
    },
    {
        "id": "WA-SEC09-001",
        "title": "Encryption in transit (TLS/IPsec)",
        "severity": "CAT1",
        "category": "data_in_transit",
        "regimes": ["wa_security"],
        "description": "All network links should use encrypted transport (TLS 1.2+, IPsec, MACsec) (SEC09-BP02).",
        "check": "encryption_on_links",
        "wa_ref": "SEC09-BP02",
    },
    # ── Incident Response ──
    {
        "id": "WA-SEC10-001",
        "title": "Pre-deployed investigation tools",
        "severity": "CAT2",
        "category": "incident_response",
        "regimes": ["wa_security"],
        "description": "Detective/Sentinel/SIEM should be deployed before incidents occur (SEC10-BP06).",
        "check": "has_centralized_logging",
        "wa_ref": "SEC10-BP06",
    },
]

# ── WA Security Node Type Detection Sets ───────────────────────────────────────

WA_THREAT_DETECTION_TYPES = {
    "aws-guardduty",
    "az-defender",
    "gcp-scc",
    "oci-cloudguard",
    "ibm-scc",
}

WA_CONFIG_MONITORING_TYPES = {
    "aws-config",
    "aws-securityhub",
    "az-policy",
    "gcp-orgpolicy",
    "oci-cloudguard",
    "ibm-scc",
}

# ── Azure MCSB (Microsoft Cloud Security Benchmark) ────────────────────────────
# 12 control families, derived from MCSB v1. Provides implementation detail
# behind the Azure Well-Architected Security Pillar.
# Reference: https://learn.microsoft.com/en-us/security/benchmark/azure/overview

MCSB_CONTROL_FAMILIES = {
    "NS": {
        "name": "Network Security",
        "controls": 10,
        "description": "Network segmentation, NSGs, Azure Firewall, Private Link, DDoS protection.",
    },
    "IM": {
        "name": "Identity Management",
        "controls": 9,
        "description": "Entra ID, MFA, Conditional Access, managed identities, SSO federation.",
    },
    "PA": {
        "name": "Privileged Access",
        "controls": 8,
        "description": "PIM just-in-time elevation, emergency access, admin workstations.",
    },
    "DP": {
        "name": "Data Protection",
        "controls": 8,
        "description": "Encryption at rest/transit, Key Vault, TLS enforcement, data classification.",
    },
    "AM": {
        "name": "Asset Management",
        "controls": 5,
        "description": "Resource inventory, tagging, unauthorized resource detection.",
    },
    "LT": {
        "name": "Logging and Threat Detection",
        "controls": 7,
        "description": "Sentinel SIEM, Defender for Cloud, diagnostic logging, activity logs.",
    },
    "IR": {
        "name": "Incident Response",
        "controls": 6,
        "description": "IR plans, Sentinel playbooks, notification contacts, post-incident review.",
    },
    "PV": {
        "name": "Posture and Vulnerability Management",
        "controls": 6,
        "description": "Defender vulnerability scanning, OS/container patching, secure configurations.",
    },
    "ES": {
        "name": "Endpoint Security",
        "controls": 4,
        "description": "EDR (Defender for Endpoint), antimalware, host-based firewall — unique to Azure.",
    },
    "BR": {
        "name": "Backup and Recovery",
        "controls": 3,
        "description": "Azure Backup, immutable vaults, RBAC for backup operations — security of backups.",
    },
    "DS": {
        "name": "DevOps Security",
        "controls": 5,
        "description": "Defender for DevOps, GitHub Advanced Security, pipeline integrity.",
    },
    "GS": {
        "name": "Governance and Strategy",
        "controls": 5,
        "description": "Management groups, Azure Policy, security roles and responsibilities.",
    },
}

MCSB_COMPLIANCE_RULES = [
    {
        "id": "MCSB-NS-001",
        "title": "Network segmentation with NSGs",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["mcsb_security"],
        "description": "VNets must use NSGs to segment traffic between subnets (MCSB NS-1, NS-2).",
        "check": "has_network_layers",
        "mcsb_ref": "NS-1",
    },
    {
        "id": "MCSB-NS-002",
        "title": "Azure Firewall or NVA for inspection",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["mcsb_security"],
        "description": "Centralized firewall for traffic inspection and threat detection (MCSB NS-4, NS-5).",
        "check": "has_network_firewall",
        "mcsb_ref": "NS-4",
    },
    {
        "id": "MCSB-NS-003",
        "title": "DDoS Protection Standard",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["mcsb_security"],
        "description": "Azure DDoS Protection Standard on VNets with public-facing resources (MCSB NS-5).",
        "check": "has_ddos_protection",
        "mcsb_ref": "NS-5",
    },
    {
        "id": "MCSB-NS-004",
        "title": "Private Link for service access",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["mcsb_security"],
        "description": "Use Private Link endpoints to access PaaS services without public internet (MCSB NS-2).",
        "check": "has_private_endpoints",
        "mcsb_ref": "NS-2",
    },
    {
        "id": "MCSB-IM-001",
        "title": "Centralized identity with Entra ID",
        "severity": "CAT1",
        "category": "identity",
        "regimes": ["mcsb_security"],
        "description": "Entra ID as centralized identity provider with MFA and Conditional Access (MCSB IM-1, IM-4).",
        "check": "has_identity_service",
        "mcsb_ref": "IM-1",
    },
    {
        "id": "MCSB-LT-001",
        "title": "Microsoft Sentinel SIEM deployed",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["mcsb_security"],
        "description": "Sentinel workspace for centralized logging, threat detection, and SOAR (MCSB LT-1, LT-4).",
        "check": "has_centralized_logging",
        "mcsb_ref": "LT-1",
    },
    {
        "id": "MCSB-LT-002",
        "title": "Defender for Cloud enabled",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["mcsb_security"],
        "description": "Microsoft Defender for Cloud CSPM + workload protection (MCSB LT-1).",
        "check": "has_threat_detection",
        "mcsb_ref": "LT-1",
    },
    {
        "id": "MCSB-DP-001",
        "title": "Key Vault for key management",
        "severity": "CAT1",
        "category": "data_protection",
        "regimes": ["mcsb_security"],
        "description": "Azure Key Vault for FIPS 140-2 L2/L3 key and secret management (MCSB DP-4, DP-6).",
        "check": "has_kms",
        "mcsb_ref": "DP-4",
    },
    {
        "id": "MCSB-ES-001",
        "title": "Endpoint protection (Defender for Endpoint)",
        "severity": "CAT2",
        "category": "compute",
        "regimes": ["mcsb_security"],
        "description": "EDR and antimalware on all VMs and containers — unique to Azure MCSB (MCSB ES-1, ES-2).",
        "check": "has_vuln_scanning",
        "mcsb_ref": "ES-1",
    },
    {
        "id": "MCSB-PV-001",
        "title": "Vulnerability scanning enabled",
        "severity": "CAT2",
        "category": "compute",
        "regimes": ["mcsb_security"],
        "description": "Defender for Servers/Containers for OS and container vulnerability scanning (MCSB PV-5).",
        "check": "has_vuln_scanning",
        "mcsb_ref": "PV-5",
    },
]

# ── GCP Security Foundations ───────────────────────────────────────────────────
# 8 security areas from Google Cloud Architecture Framework + Security
# Foundations Blueprint. No formal control IDs — uses synthetic GCP-SEC-xxx.
# Reference: https://cloud.google.com/architecture/framework/security

GCP_SECURITY_AREAS = {
    "iam": {"name": "Identity and Access Management", "code": "GCP-IAM"},
    "resource_hierarchy": {"name": "Resource Hierarchy and Org Policies", "code": "GCP-RH"},
    "network": {"name": "Network Security", "code": "GCP-NS"},
    "compute": {"name": "Compute and Container Security", "code": "GCP-CS"},
    "data": {"name": "Data Security", "code": "GCP-DS"},
    "logging": {"name": "Logging and Detection", "code": "GCP-LD"},
    "app_security": {"name": "Application Security", "code": "GCP-AS"},
    "supply_chain": {"name": "Supply Chain Security", "code": "GCP-SC"},
}

GCP_SECURITY_COMPLIANCE_RULES = [
    {
        "id": "GCP-NS-001",
        "title": "VPC with firewall rules (deny-all default)",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["gcp_security"],
        "description": "Shared VPC with hierarchical firewall policies; default deny-all ingress.",
        "check": "has_network_firewall",
    },
    {
        "id": "GCP-NS-002",
        "title": "Cloud Armor for DDoS/WAF",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["gcp_security"],
        "description": "Cloud Armor policies on all external load balancers for DDoS and WAF.",
        "check": "has_ddos_protection",
    },
    {
        "id": "GCP-NS-003",
        "title": "VPC Service Controls perimeter",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["gcp_security"],
        "description": "VPC Service Controls to prevent data exfiltration via API — unique to GCP.",
        "check": "has_network_layers",
    },
    {
        "id": "GCP-IAM-001",
        "title": "Workforce Identity Federation",
        "severity": "CAT1",
        "category": "identity",
        "regimes": ["gcp_security"],
        "description": "Centralized identity via Workforce Identity Federation for CAC/PIV.",
        "check": "has_identity_service",
    },
    {
        "id": "GCP-LD-001",
        "title": "Security Command Center Premium",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["gcp_security"],
        "description": "SCC Premium for threat detection, vulnerability scanning, and compliance.",
        "check": "has_threat_detection",
    },
    {
        "id": "GCP-LD-002",
        "title": "Cloud Audit Logs and log sinks",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["gcp_security"],
        "description": "Admin/Data/System event audit logs with sinks to BigQuery/GCS.",
        "check": "has_centralized_logging",
    },
    {
        "id": "GCP-DS-001",
        "title": "Cloud KMS/HSM for key management",
        "severity": "CAT1",
        "category": "data_protection",
        "regimes": ["gcp_security"],
        "description": "CMEK via Cloud KMS; Cloud HSM for FIPS 140-2 L3.",
        "check": "has_kms",
    },
    {
        "id": "GCP-RH-001",
        "title": "Organization Policies for governance",
        "severity": "CAT2",
        "category": "foundations",
        "regimes": ["gcp_security"],
        "description": "Org policies for resource location, service restrictions, and public access.",
        "check": "multi_vpc_isolation",
    },
    {
        "id": "GCP-SC-001",
        "title": "Binary Authorization for supply chain",
        "severity": "CAT2",
        "category": "app_security",
        "regimes": ["gcp_security"],
        "description": "Binary Authorization for container image attestation — SLSA compliance.",
        "check": "iac_deployed",
    },
]

# ── OCI Security Best Practices ────────────────────────────────────────────────
# Based on CIS Oracle Cloud Infrastructure Foundations Benchmark v2.0 +
# Cloud Guard detector recipes. References OCI-specific features.
# Reference: https://www.cisecurity.org/benchmark/oracle_cloud

OCI_SECURITY_AREAS = {
    "iam": {"name": "Identity and Access Management", "code": "OCI-IAM"},
    "network": {"name": "Network Security", "code": "OCI-NS"},
    "compute": {"name": "Compute Security", "code": "OCI-CS"},
    "data": {"name": "Data Protection", "code": "OCI-DP"},
    "monitoring": {"name": "Security Monitoring", "code": "OCI-SM"},
    "max_security": {"name": "Maximum Security Zones", "code": "OCI-MSZ"},
    "cloud_guard": {"name": "Cloud Guard Configuration", "code": "OCI-CG"},
}

OCI_SECURITY_COMPLIANCE_RULES = [
    {
        "id": "OCI-NS-001",
        "title": "Network Firewall in VDSS VCN",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["oci_security"],
        "description": "OCI Network Firewall (Palo Alto PaaS) for IDS/IPS and threat prevention.",
        "check": "has_network_firewall",
    },
    {
        "id": "OCI-NS-002",
        "title": "DRG for centralized routing",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["oci_security"],
        "description": "Dynamic Routing Gateway as hub for all VCN-to-VCN and on-prem traffic.",
        "check": "has_transit_hub",
    },
    {
        "id": "OCI-IAM-001",
        "title": "Identity Domains with MFA",
        "severity": "CAT1",
        "category": "identity",
        "regimes": ["oci_security"],
        "description": "OCI Identity Domains with CAC/PIV via X.509 certificate auth.",
        "check": "has_identity_service",
    },
    {
        "id": "OCI-CG-001",
        "title": "Cloud Guard enabled with detector recipes",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["oci_security"],
        "description": "Cloud Guard target with config and activity detector recipes for posture management.",
        "check": "has_threat_detection",
    },
    {
        "id": "OCI-SM-001",
        "title": "OCI Audit immutable logging",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["oci_security"],
        "description": "OCI Audit service for immutable API activity logging (NIST AU controls).",
        "check": "has_centralized_logging",
    },
    {
        "id": "OCI-DP-001",
        "title": "OCI Vault with HSM key",
        "severity": "CAT1",
        "category": "data_protection",
        "regimes": ["oci_security"],
        "description": "OCI Vault (FIPS 140-2 L3) for encryption key management; Dedicated HSM for IL5.",
        "check": "has_kms",
    },
    {
        "id": "OCI-MSZ-001",
        "title": "Maximum Security Zones for critical compartments",
        "severity": "CAT2",
        "category": "foundations",
        "regimes": ["oci_security"],
        "description": "Maximum Security Zones enforce immutable security policies — unique to OCI.",
        "check": "multi_vpc_isolation",
    },
    {
        "id": "OCI-CS-001",
        "title": "Vulnerability Scanning Service",
        "severity": "CAT2",
        "category": "compute",
        "regimes": ["oci_security"],
        "description": "OCI VSS for host and container vulnerability scanning.",
        "check": "has_vuln_scanning",
    },
]

# ── IBM Cloud Security & Compliance ────────────────────────────────────────────
# Based on IBM Cloud Security Best Practices v2.0 profile in SCC +
# CIS IBM Cloud Foundations Benchmark. Maps to NIST 800-53 families.
# Reference: https://cloud.ibm.com/docs/security-compliance

IBM_SECURITY_AREAS = {
    "iam": {"name": "Access Control", "code": "IBM-AC", "nist": "AC"},
    "network": {"name": "System and Communications Protection", "code": "IBM-SC", "nist": "SC"},
    "logging": {"name": "Audit and Accountability", "code": "IBM-AU", "nist": "AU"},
    "data": {"name": "Data Protection", "code": "IBM-DP", "nist": "SC/MP"},
    "compute": {"name": "System Integrity", "code": "IBM-SI", "nist": "SI"},
    "config": {"name": "Configuration Management", "code": "IBM-CM", "nist": "CM"},
}

IBM_SECURITY_COMPLIANCE_RULES = [
    {
        "id": "IBM-SC-001",
        "title": "VPC with security groups (deny-all default)",
        "severity": "CAT1",
        "category": "network",
        "regimes": ["ibm_security"],
        "description": "VPC security groups with default-deny; Transit Gateway for inter-VPC routing.",
        "check": "has_network_firewall",
    },
    {
        "id": "IBM-SC-002",
        "title": "Transit Gateway for network segmentation",
        "severity": "CAT2",
        "category": "network",
        "regimes": ["ibm_security"],
        "description": "Transit Gateway connecting management and workload VPCs for controlled routing.",
        "check": "has_transit_hub",
    },
    {
        "id": "IBM-AC-001",
        "title": "IAM with access groups and MFA",
        "severity": "CAT1",
        "category": "identity",
        "regimes": ["ibm_security"],
        "description": "IBM Cloud IAM with access groups, resource groups, and MFA enforcement.",
        "check": "has_identity_service",
    },
    {
        "id": "IBM-AU-001",
        "title": "Activity Tracker for API audit",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["ibm_security"],
        "description": "Activity Tracker for API audit logging; Log Analysis for centralized log aggregation.",
        "check": "has_centralized_logging",
    },
    {
        "id": "IBM-AU-002",
        "title": "Security & Compliance Center monitoring",
        "severity": "CAT1",
        "category": "detection",
        "regimes": ["ibm_security"],
        "description": "SCC with IBM Cloud Best Practices profile for continuous posture monitoring.",
        "check": "has_threat_detection",
    },
    {
        "id": "IBM-DP-001",
        "title": "Key Protect or HPCS for encryption",
        "severity": "CAT1",
        "category": "data_protection",
        "regimes": ["ibm_security"],
        "description": "Key Protect for BYOK; HPCS for FIPS 140-2 L4 (highest available on any CSP).",
        "check": "has_kms",
    },
    {
        "id": "IBM-SI-001",
        "title": "SCC vulnerability assessment",
        "severity": "CAT2",
        "category": "compute",
        "regimes": ["ibm_security"],
        "description": "SCC vulnerability assessment for container and host scanning.",
        "check": "has_vuln_scanning",
    },
]

# ── NDC Container Node Type (Feature 1) ───────────────────────────────────────
# Docker containers as first-class topology nodes. Used by the NDC palette and
# by tools/network/container_node.py for validation.
NODE_TYPE_CONTAINER = "container"

CONTAINER_IMAGE_WHITELIST = [
    # Routing & switching
    "quay.io/frrouting/frr:latest",
    "quay.io/frrouting/frr:9.1",
    "nicolaka/netshoot:latest",
    # Sensors / DPI
    "jasonish/suricata:latest",
    "cyberreboot/zeek:latest",
    # Proxies / gateways
    "nginx:alpine",
    "haproxy:lts-alpine",
    "envoyproxy/envoy:v1.28-latest",
    # DNS / pihole
    "pihole/pihole:latest",
    "coredns/coredns:1.11.1",
    # Generic tools
    "alpine:3.19",
    "debian:12-slim",
]

CONTAINER_PROPERTIES_SCHEMA = {
    "image": {"type": "str", "required": True, "whitelist": CONTAINER_IMAGE_WHITELIST},
    "cmd": {"type": "list", "required": False},
    "environment": {"type": "dict", "required": False},
    "volumes": {"type": "list", "required": False},
    "network_mode": {"type": "str", "default": "bridge"},
    "privileged": {"type": "bool", "default": False},
}

# ── Network Twin: Intent Validation Rules ────────────────────────────────────
INTENT_RULES = [
    {"id": "reach-prod",       "label": "Prod Reachability",         "desc": "All production services must be reachable from their expected ingress paths"},
    {"id": "no-direct-internet","label": "No Direct Internet",       "desc": "No workload node may have a direct unmediated path to the public internet"},
    {"id": "acl-compliance",   "label": "ACL Compliance",             "desc": "All ACL changes must comply with the approved ports/protocols/services matrix"},
    {"id": "il-boundary",      "label": "IL Boundary Isolation",      "desc": "CUI/IL4+ resources must not have data-path adjacency to IL2 resources without a cross-domain solution"},
    {"id": "no-unencrypted",   "label": "Encryption in Transit",      "desc": "No plaintext protocols (HTTP, Telnet, FTP) allowed across trust-boundary links"},
    {"id": "redundancy",       "label": "Redundancy (N+1)",           "desc": "Critical paths must have at least one redundant link; single-path segments must be flagged"},
]

# ── Ontology Mapping (network -> ICDEV network ontology) ────────────────────
NETWORK_ONTOLOGY_MAP: dict[str, str] = {
    # AWS Network
    "aws-ad": "https://icdev.dev/ontology/network#AWS.Ad",
    "aws-alb": "https://icdev.dev/ontology/network#AWS.Alb",
    "aws-appstream": "https://icdev.dev/ontology/network#AWS.Appstream",
    "aws-cgw": "https://icdev.dev/ontology/network#AWS.Cgw",
    "aws-cloudfront": "https://icdev.dev/ontology/network#AWS.Cloudfront",
    "aws-cloudwan": "https://icdev.dev/ontology/network#AWS.Cloudwan",
    "aws-config": "https://icdev.dev/ontology/network#AWS.Config",
    "aws-ct": "https://icdev.dev/ontology/network#AWS.Ct",
    "aws-dx": "https://icdev.dev/ontology/network#AWS.Dx",
    "aws-dx-gw": "https://icdev.dev/ontology/network#AWS.DxGw",
    "aws-flowlogs": "https://icdev.dev/ontology/network#AWS.Flowlogs",
    "aws-ga": "https://icdev.dev/ontology/network#AWS.Ga",
    "aws-guardduty": "https://icdev.dev/ontology/network#AWS.Guardduty",
    "aws-gw-ep": "https://icdev.dev/ontology/network#AWS.GwEp",
    "aws-gwlb": "https://icdev.dev/ontology/network#AWS.Gwlb",
    "aws-idc": "https://icdev.dev/ontology/network#AWS.Idc",
    "aws-inspector": "https://icdev.dev/ontology/network#AWS.Inspector",
    "aws-kms": "https://icdev.dev/ontology/network#AWS.Kms",
    "aws-localzone": "https://icdev.dev/ontology/network#AWS.Localzone",
    "aws-netmgr": "https://icdev.dev/ontology/network#AWS.Netmgr",
    "aws-nfw": "https://icdev.dev/ontology/network#AWS.Nfw",
    "aws-nlb": "https://icdev.dev/ontology/network#AWS.Nlb",
    "aws-outpost": "https://icdev.dev/ontology/network#AWS.Outpost",
    "aws-privateca": "https://icdev.dev/ontology/network#AWS.Privateca",
    "aws-privatelink": "https://icdev.dev/ontology/network#AWS.Privatelink",
    "aws-r53": "https://icdev.dev/ontology/network#AWS.R53",
    "aws-reach": "https://icdev.dev/ontology/network#AWS.Reach",
    "aws-securityhub": "https://icdev.dev/ontology/network#AWS.Securityhub",
    "aws-shield": "https://icdev.dev/ontology/network#AWS.Shield",
    "aws-ssm": "https://icdev.dev/ontology/network#AWS.Ssm",
    "aws-subnet": "https://icdev.dev/ontology/network#AWS.Subnet",
    "aws-tgw": "https://icdev.dev/ontology/network#AWS.Tgw",
    "aws-tgw-rt": "https://icdev.dev/ontology/network#AWS.TgwRt",
    "aws-vgw": "https://icdev.dev/ontology/network#AWS.Vgw",
    "aws-vpc": "https://icdev.dev/ontology/network#AWS.Vpc",
    "aws-vpn": "https://icdev.dev/ontology/network#AWS.Vpn",
    "aws-vpn-ha": "https://icdev.dev/ontology/network#AWS.VpnHa",
    "aws-waf": "https://icdev.dev/ontology/network#AWS.Waf",
    "aws-workspaces": "https://icdev.dev/ontology/network#AWS.Workspaces",
    # Azure Network
    "az-appgw": "https://icdev.dev/ontology/network#Azure.Appgw",
    "az-bastion": "https://icdev.dev/ontology/network#Azure.Bastion",
    "az-crosslb": "https://icdev.dev/ontology/network#Azure.Crosslb",
    "az-ddos": "https://icdev.dev/ontology/network#Azure.Ddos",
    "az-defender": "https://icdev.dev/ontology/network#Azure.Defender",
    "az-dns": "https://icdev.dev/ontology/network#Azure.Dns",
    "az-entra": "https://icdev.dev/ontology/network#Azure.Entra",
    "az-er": "https://icdev.dev/ontology/network#Azure.Er",
    "az-er-global": "https://icdev.dev/ontology/network#Azure.ErGlobal",
    "az-ergw": "https://icdev.dev/ontology/network#Azure.Ergw",
    "az-flowlogs": "https://icdev.dev/ontology/network#Azure.Flowlogs",
    "az-front": "https://icdev.dev/ontology/network#Azure.Front",
    "az-fw": "https://icdev.dev/ontology/network#Azure.Fw",
    "az-keyvault": "https://icdev.dev/ontology/network#Azure.Keyvault",
    "az-lng": "https://icdev.dev/ontology/network#Azure.Lng",
    "az-monitor": "https://icdev.dev/ontology/network#Azure.Monitor",
    "az-netwatcher": "https://icdev.dev/ontology/network#Azure.Netwatcher",
    "az-nsg": "https://icdev.dev/ontology/network#Azure.Nsg",
    "az-policy": "https://icdev.dev/ontology/network#Azure.Policy",
    "az-privatelink": "https://icdev.dev/ontology/network#Azure.Privatelink",
    "az-route-server": "https://icdev.dev/ontology/network#Azure.RouteServer",
    "az-sentinel": "https://icdev.dev/ontology/network#Azure.Sentinel",
    "az-stack": "https://icdev.dev/ontology/network#Azure.Stack",
    "az-subnet": "https://icdev.dev/ontology/network#Azure.Subnet",
    "az-vnet": "https://icdev.dev/ontology/network#Azure.Vnet",
    "az-vnet-peer": "https://icdev.dev/ontology/network#Azure.VnetPeer",
    "az-vpn-gw": "https://icdev.dev/ontology/network#Azure.VpnGw",
    "az-vwan": "https://icdev.dev/ontology/network#Azure.Vwan",
    # GCP Network
    "gcp-armor": "https://icdev.dev/ontology/network#GCP.Armor",
    "gcp-assured": "https://icdev.dev/ontology/network#GCP.Assured",
    "gcp-cdn": "https://icdev.dev/ontology/network#GCP.Cdn",
    "gcp-classic-vpn": "https://icdev.dev/ontology/network#GCP.ClassicVpn",
    "gcp-dns": "https://icdev.dev/ontology/network#GCP.Dns",
    "gcp-flowlogs": "https://icdev.dev/ontology/network#GCP.Flowlogs",
    "gcp-gdc": "https://icdev.dev/ontology/network#GCP.Gdc",
    "gcp-gfe": "https://icdev.dev/ontology/network#GCP.Gfe",
    "gcp-ha-vpn": "https://icdev.dev/ontology/network#GCP.HaVpn",
    "gcp-ic": "https://icdev.dev/ontology/network#GCP.Ic",
    "gcp-ic-partner": "https://icdev.dev/ontology/network#GCP.IcPartner",
    "gcp-kms": "https://icdev.dev/ontology/network#GCP.Kms",
    "gcp-lb": "https://icdev.dev/ontology/network#GCP.Lb",
    "gcp-nat": "https://icdev.dev/ontology/network#GCP.Nat",
    "gcp-ncc": "https://icdev.dev/ontology/network#GCP.Ncc",
    "gcp-ncc-spoke": "https://icdev.dev/ontology/network#GCP.NccSpoke",
    "gcp-nic": "https://icdev.dev/ontology/network#GCP.Nic",
    "gcp-orgpolicy": "https://icdev.dev/ontology/network#GCP.Orgpolicy",
    "gcp-psc": "https://icdev.dev/ontology/network#GCP.Psc",
    "gcp-router": "https://icdev.dev/ontology/network#GCP.Router",
    "gcp-scc": "https://icdev.dev/ontology/network#GCP.Scc",
    "gcp-subnet": "https://icdev.dev/ontology/network#GCP.Subnet",
    "gcp-vdi": "https://icdev.dev/ontology/network#GCP.Vdi",
    "gcp-vpc": "https://icdev.dev/ontology/network#GCP.Vpc",
    "gcp-vpn": "https://icdev.dev/ontology/network#GCP.Vpn",
    # OCI Network
    "oci-audit": "https://icdev.dev/ontology/network#OCI.Audit",
    "oci-cloudguard": "https://icdev.dev/ontology/network#OCI.Cloudguard",
    "oci-cpe": "https://icdev.dev/ontology/network#OCI.Cpe",
    "oci-ddos": "https://icdev.dev/ontology/network#OCI.Ddos",
    "oci-dedicated": "https://icdev.dev/ontology/network#OCI.Dedicated",
    "oci-drg": "https://icdev.dev/ontology/network#OCI.Drg",
    "oci-fc": "https://icdev.dev/ontology/network#OCI.Fc",
    "oci-fc-vc": "https://icdev.dev/ontology/network#OCI.FcVc",
    "oci-fd": "https://icdev.dev/ontology/network#OCI.Fd",
    "oci-flowlogs": "https://icdev.dev/ontology/network#OCI.Flowlogs",
    "oci-identity": "https://icdev.dev/ontology/network#OCI.Identity",
    "oci-ipsec": "https://icdev.dev/ontology/network#OCI.Ipsec",
    "oci-lb": "https://icdev.dev/ontology/network#OCI.Lb",
    "oci-nfw": "https://icdev.dev/ontology/network#OCI.Nfw",
    "oci-nsg": "https://icdev.dev/ontology/network#OCI.Nsg",
    "oci-pathanalyzer": "https://icdev.dev/ontology/network#OCI.Pathanalyzer",
    "oci-subnet": "https://icdev.dev/ontology/network#OCI.Subnet",
    "oci-vault": "https://icdev.dev/ontology/network#OCI.Vault",
    "oci-vcn": "https://icdev.dev/ontology/network#OCI.Vcn",
    "oci-vss": "https://icdev.dev/ontology/network#OCI.Vss",
    "oci-waf": "https://icdev.dev/ontology/network#OCI.Waf",
    # IBM Network
    "ibm-appid": "https://icdev.dev/ontology/network#IBM.Appid",
    "ibm-cis": "https://icdev.dev/ontology/network#IBM.Cis",
    "ibm-dl": "https://icdev.dev/ontology/network#IBM.Dl",
    "ibm-dl-con": "https://icdev.dev/ontology/network#IBM.DlCon",
    "ibm-dl-ded": "https://icdev.dev/ontology/network#IBM.DlDed",
    "ibm-flowlogs": "https://icdev.dev/ontology/network#IBM.Flowlogs",
    "ibm-hpcs": "https://icdev.dev/ontology/network#IBM.Hpcs",
    "ibm-keyprotect": "https://icdev.dev/ontology/network#IBM.Keyprotect",
    "ibm-lb": "https://icdev.dev/ontology/network#IBM.Lb",
    "ibm-satellite": "https://icdev.dev/ontology/network#IBM.Satellite",
    "ibm-scc": "https://icdev.dev/ontology/network#IBM.Scc",
    "ibm-subnet": "https://icdev.dev/ontology/network#IBM.Subnet",
    "ibm-tg": "https://icdev.dev/ontology/network#IBM.Tg",
    "ibm-vpc": "https://icdev.dev/ontology/network#IBM.Vpc",
    "ibm-vpn": "https://icdev.dev/ontology/network#IBM.Vpn",
    # Multi-Cloud
    "cloud-peering": "https://icdev.dev/ontology/network#CloudPeering",
    "cloud-region": "https://icdev.dev/ontology/network#CloudRegion",
    "equinix-fabric": "https://icdev.dev/ontology/network#EquinixFabric",
    "internet-exchange": "https://icdev.dev/ontology/network#InternetExchange",
    "megaport-mcr": "https://icdev.dev/ontology/network#MegaportMcr",
    "sase-pop": "https://icdev.dev/ontology/network#SasePop",
    "sdwan-overlay": "https://icdev.dev/ontology/network#SdwanOverlay",
    # DoD Network
    "dod-bcap": "https://icdev.dev/ontology/network#DOD.Bcap",
    "dod-c2e-dns-private": "https://icdev.dev/ontology/network#DOD.C2EDnsPrivate",
    "dod-c2e-expressroute": "https://icdev.dev/ontology/network#DOD.C2EExpressroute",
    "dod-c2e-vnet": "https://icdev.dev/ontology/network#DOD.C2EVnet",
    "dod-c2s-direct-connect": "https://icdev.dev/ontology/network#DOD.C2SDirectConnect",
    "dod-c2s-dns-phz": "https://icdev.dev/ontology/network#DOD.C2SDnsPhz",
    "dod-c2s-tgw": "https://icdev.dev/ontology/network#DOD.C2STgw",
    "dod-c2s-vpc": "https://icdev.dev/ontology/network#DOD.C2SVpc",
    "dod-cds": "https://icdev.dev/ontology/network#DOD.Cds",
    "dod-jwics-backbone": "https://icdev.dev/ontology/network#DOD.JwicsBackbone",
    "dod-jwics-dns": "https://icdev.dev/ontology/network#DOD.JwicsDns",
    "dod-jwics-gateway": "https://icdev.dev/ontology/network#DOD.JwicsGateway",
    "dod-jwics-mail-relay": "https://icdev.dev/ontology/network#DOD.JwicsMailRelay",
    "dod-niprnet-onramp": "https://icdev.dev/ontology/network#DOD.NiprnetOnramp",
    "dod-scif-lan": "https://icdev.dev/ontology/network#DOD.ScifLan",
    "dod-secret-bcap": "https://icdev.dev/ontology/network#DOD.SecretBcap",
    "dod-tccm": "https://icdev.dev/ontology/network#DOD.Tccm",
    "dod-type1-encryptor": "https://icdev.dev/ontology/network#DOD.Type1Encryptor",
    "dod-vdms": "https://icdev.dev/ontology/network#DOD.Vdms",
    "dod-vdss": "https://icdev.dev/ontology/network#DOD.Vdss",
    # Colocation
    "cabinet": "https://icdev.dev/ontology/network#Cabinet",
    "cage": "https://icdev.dev/ontology/network#Cage",
    "cross-connect": "https://icdev.dev/ontology/network#CrossConnect",
    "demarc": "https://icdev.dev/ontology/network#Demarc",
    "meet-me-room": "https://icdev.dev/ontology/network#MeetMeRoom",
    # VDI
    "avd-hostpool": "https://icdev.dev/ontology/network#AVD.Hostpool",
    "avd-workspace": "https://icdev.dev/ontology/network#AVD.Workspace",
    "citrix-cloud": "https://icdev.dev/ontology/network#CitrixCloud",
    "horizon-cloud": "https://icdev.dev/ontology/network#HorizonCloud",
    "thin-client": "https://icdev.dev/ontology/network#ThinClient",
    "vdi-connection-broker": "https://icdev.dev/ontology/network#VdiConnectionBroker",
    "vdi-gateway": "https://icdev.dev/ontology/network#VdiGateway",
    "vdi-gpu-host": "https://icdev.dev/ontology/network#VdiGpuHost",
    "vdi-image-store": "https://icdev.dev/ontology/network#VdiImageStore",
    "vdi-license-server": "https://icdev.dev/ontology/network#VdiLicenseServer",
    "vdi-profile-server": "https://icdev.dev/ontology/network#VdiProfileServer",
    "vdi-session-host": "https://icdev.dev/ontology/network#VdiSessionHost",
    "vdi-web-client": "https://icdev.dev/ontology/network#VdiWebClient",
    "zero-client": "https://icdev.dev/ontology/network#ZeroClient",
    # Edge Compute
    "cdn-pop": "https://icdev.dev/ontology/network#CdnPop",
    "edge-cluster": "https://icdev.dev/ontology/network#EdgeCluster",
    "edge-gateway": "https://icdev.dev/ontology/network#EdgeGateway",
    "fog-node": "https://icdev.dev/ontology/network#FogNode",
    "kiosk": "https://icdev.dev/ontology/network#Kiosk",
    "mec-node": "https://icdev.dev/ontology/network#MecNode",
    # Digital Twin
    "twin-blast-radius": "https://icdev.dev/ontology/network#TwinBlastRadius",
    "twin-intent-validator": "https://icdev.dev/ontology/network#TwinIntentValidator",
    "twin-network": "https://icdev.dev/ontology/network#TwinNetwork",
    "twin-topo-simulator": "https://icdev.dev/ontology/network#TwinTopoSimulator",
}

# ── Partner Registry constants ──────────────────────────────────────────────
PARTNER_TYPES = ['isp', 'carrier', 'cloud', 'content', 'enterprise', 'ix']
PARTNER_STATUSES = ['active', 'suspended', 'terminated']

# ── Diagram Analysis Industries ──────────────────────────────────────────────
# Keys match _INDUSTRY_LENS keys in diagram_analysis.py.
DIAGRAM_ANALYSIS_INDUSTRIES = {
    "dod_il4": {
        "label": "DoD/IC — IL4 (CUI / FedRAMP High)",
        "frameworks": ["nist_800_53", "cmmc_level_2", "fedramp_high", "il4", "cnssi_1253", "csa_ccm", "nist_800_144"],
    },
    "dod_il5": {
        "label": "DoD/IC — IL5 (NIPR Secret-Enclave)",
        "frameworks": ["nist_800_53", "cmmc_level_3", "fedramp_high", "il5", "cnssi_1253", "csa_ccm"],
    },
    "dod_il6": {
        "label": "DoD/IC — IL6 SECRET",
        "frameworks": ["nist_800_53", "il6", "cnssi_1253", "nsa_type1"],
    },
    "healthcare": {
        "label": "Healthcare (HIPAA / HITRUST)",
        "frameworks": ["hipaa", "hitrust", "nist_800_53", "csa_ccm"],
    },
    "financial": {
        "label": "Financial (PCI-DSS / SOC 2)",
        "frameworks": ["pci_dss", "soc2", "nist_800_53", "csa_ccm"],
    },
    "commercial": {
        "label": "Commercial / Enterprise (ISO 27001)",
        "frameworks": ["nist_800_53", "iso_27001", "owasp", "csa_ccm", "nist_800_144"],
    },
}

