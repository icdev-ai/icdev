# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Compliance Audit Engine.

Pure functions for running compliance checks against network topologies.
Checks topologies against STIG, FedRAMP/FISMA, CMMC, FIPS, CJIS, ICD 503,
CNSS 1253, and Zero Trust (NIST 800-207) regimes.

No Flask dependency — takes graph data and returns results.
"""

from collections import deque

from tools.network.constants import (
    WA_SECURITY_COMPLIANCE_RULES,
    MCSB_COMPLIANCE_RULES,
    GCP_SECURITY_COMPLIANCE_RULES,
    OCI_SECURITY_COMPLIANCE_RULES,
    IBM_SECURITY_COMPLIANCE_RULES,
)


# ── Compliance Regimes & Rule Definitions ─────────────────────────────────────
# 8 regimes with crosswalk mapping. Each rule is deterministic (no LLM needed).

COMPLIANCE_REGIMES = {
    "fisma_high": {"name": "FISMA High", "framework": "NIST 800-53 Rev 5", "baseline": "High"},
    "stig": {"name": "DISA STIG", "framework": "DoD STIG", "baseline": "Network"},
    "fips": {"name": "FIPS 140-2/3", "framework": "FIPS", "baseline": "Level 2"},
    "zta": {"name": "Zero Trust (NIST 800-207)", "framework": "NIST 800-207", "baseline": "Advanced"},
    "cjis": {"name": "CJIS Security Policy", "framework": "FBI CJIS", "baseline": "5.9.1"},
    "icd503": {"name": "ICD 503 (IC)", "framework": "ODNI ICD 503", "baseline": "Full"},
    "cnss1253": {"name": "CNSS 1253 (NSS)", "framework": "CNSS", "baseline": "High"},
    "wa_security": {
        "name": "Well-Architected Security Pillar",
        "framework": "AWS Well-Architected",
        "baseline": "SEC01-SEC11",
    },
    "mcsb_security": {"name": "Microsoft Cloud Security Benchmark", "source": "MCSB v1", "scope": "Azure"},
    "gcp_security": {"name": "GCP Security Foundations", "source": "Architecture Framework", "scope": "GCP"},
    "oci_security": {"name": "OCI Security Best Practices", "source": "CIS OCI v2.0", "scope": "OCI"},
    "ibm_security": {"name": "IBM Cloud Security & Compliance", "source": "SCC Best Practices v2.0", "scope": "IBM"},
    "pci_dss": {"name": "PCI-DSS v4.0", "framework": "PCI-DSS", "baseline": "Network Segmentation"},
    "hipaa": {"name": "HIPAA Security Rule", "framework": "HIPAA", "baseline": "Technical Safeguards"},
    "iot_ot": {"name": "IoT/OT Security", "framework": "NIST 800-82 / IEC 62443", "baseline": "Zone & Conduit"},
    "k8s": {"name": "Kubernetes Network Security", "framework": "CIS K8s Benchmark", "baseline": "Network Policy"},
    "dns_bgp": {"name": "DNS & BGP Security", "framework": "NIST 800-81 / RFC 7454", "baseline": "Routing Security"},
}

# Crosswalk: rule_id -> list of regimes it applies to
# Rules run once, findings tagged with all applicable regimes
COMPLIANCE_RULES = (
    [
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
        # ── Hybrid Cloud Networking (AWS Well-Architected Hybrid Networking Lens) ──
        {
            "id": "NET-HYB-001",
            "title": "BFD required on dedicated interconnect VIFs",
            "severity": "CAT1",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "cnss1253"],
            "description": "BFD (Bidirectional Forwarding Detection) must be enabled on all DX/ER/IC/FC VIFs for sub-second (<1s) failover. Default BGP hold timer is 90 seconds — unacceptable for production (ARC322).",
            "check": "bfd_on_dx",
        },
        {
            "id": "NET-HYB-002",
            "title": "Dedicated circuit requires VPN backup",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "cjis", "cnss1253"],
            "description": "Each dedicated interconnect (DX/ER/IC/FC) must have an IPSec VPN backup path for automatic failover if the circuit fails (NIST CP-8 alternate telecom services).",
            "check": "dx_vpn_backup",
        },
        {
            "id": "NET-HYB-003",
            "title": "DX/ER at 2+ diverse locations for 99.99% SLA",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "cnss1253"],
            "description": "Maximum resiliency (99.99% SLA) requires connections at 2+ geographically diverse colocation facilities, with at least one co-located with the workload Region (AWS DX Resiliency Toolkit).",
            "check": "dx_diverse_locations",
        },
        {
            "id": "NET-HYB-004",
            "title": "Transit hub for multi-VPC connectivity",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "zta"],
            "description": "Multiple VPCs/VNets should connect via a transit hub (TGW/vWAN/NCC/DRG) rather than full-mesh peering — reduces complexity and enforces centralized security policy.",
            "check": "transit_hub_for_multi_vpc",
        },
        {
            "id": "NET-HYB-005",
            "title": "Flow logs enabled on all cloud virtual networks",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "zta", "cjis", "icd503"],
            "description": "VPC/VNet/VCN Flow Logs must be enabled for traffic visibility and audit trail per NIST AU-3 (content of audit records) and SI-4 (information system monitoring).",
            "check": "cloud_flow_logs",
        },
        {
            "id": "NET-HYB-006",
            "title": "Private endpoints for cloud service access",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "zta", "icd503"],
            "description": "Cloud services should be accessed via private endpoints (PrivateLink/PSC/Service Gateway) — avoid routing through public internet (NIST SC-7 boundary protection, ZTA principle of least exposure).",
            "check": "private_endpoints",
        },
        {
            "id": "NET-HYB-007",
            "title": "DDoS protection on internet-facing endpoints",
            "severity": "CAT2",
            "category": "hybrid",
            "regimes": ["fisma_high", "stig", "cjis", "cnss1253"],
            "description": "Internet-facing cloud resources must have DDoS protection enabled (Shield/DDoS Protection/Cloud Armor) per NIST SC-5 (denial of service protection).",
            "check": "ddos_protection",
        },
        # ── PCI-DSS Network Segmentation ─────────────────────────────────────────
        {
            "id": "NET-PCI-001",
            "title": "CDE isolated from non-CDE networks",
            "severity": "CAT1",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "Cardholder Data Environment (CDE) nodes must reside in a dedicated boundary/segment, isolated from non-CDE networks (PCI-DSS Req 1.3).",
            "check": "cde_isolation",
        },
        {
            "id": "NET-PCI-002",
            "title": "Firewall between CDE and untrusted networks",
            "severity": "CAT1",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "A firewall must exist between the CDE boundary and any untrusted network including the internet (PCI-DSS Req 1.3.1).",
            "check": "cde_firewall",
        },
        {
            "id": "NET-PCI-003",
            "title": "No direct internet access to CDE",
            "severity": "CAT1",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "No direct edge should exist from internet-facing nodes to CDE-labeled nodes without traversing a DMZ/firewall (PCI-DSS Req 1.3.2).",
            "check": "cde_no_direct_internet",
        },
        {
            "id": "NET-PCI-004",
            "title": "CDE network monitoring enabled",
            "severity": "CAT2",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "IDS/IPS or SIEM must be connected to the CDE zone to monitor for intrusions (PCI-DSS Req 11.4).",
            "check": "cde_monitoring",
        },
        {
            "id": "NET-PCI-005",
            "title": "Wireless networks isolated from CDE",
            "severity": "CAT2",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "Wireless access points (WAP) must not be in the same segment as CDE nodes — wireless must be isolated from cardholder data (PCI-DSS Req 1.2.3).",
            "check": "cde_wireless_isolation",
        },
        {
            "id": "NET-PCI-006",
            "title": "CDE access logging enabled",
            "severity": "CAT2",
            "category": "pci",
            "regimes": ["pci_dss"],
            "description": "A SIEM or centralized log collector must be present in the topology to capture CDE access logs (PCI-DSS Req 10.1).",
            "check": "cde_logging",
        },
        # ── HIPAA PHI Network ─────────────────────────────────────────────────────
        {
            "id": "NET-HIPAA-001",
            "title": "PHI data flows encrypted in transit",
            "severity": "CAT1",
            "category": "hipaa",
            "regimes": ["hipaa"],
            "description": "All network edges touching PHI-labeled nodes must use encryption (IPSec, TLS, MACsec) to protect ePHI in transit (HIPAA §164.312(e)(1)).",
            "check": "phi_encrypted",
        },
        {
            "id": "NET-HIPAA-002",
            "title": "PHI zone access controlled",
            "severity": "CAT1",
            "category": "hipaa",
            "regimes": ["hipaa"],
            "description": "A firewall or ACL must exist between the PHI boundary/zone and all other network zones (HIPAA §164.312(a)(1) access control).",
            "check": "phi_access_control",
        },
        {
            "id": "NET-HIPAA-003",
            "title": "PHI access audit logging",
            "severity": "CAT1",
            "category": "hipaa",
            "regimes": ["hipaa"],
            "description": "A SIEM or audit logging system must be connected to the PHI zone to record access activity (HIPAA §164.312(b) audit controls).",
            "check": "phi_audit_logging",
        },
        {
            "id": "NET-HIPAA-004",
            "title": "BAA partner connections encrypted",
            "severity": "CAT2",
            "category": "hipaa",
            "regimes": ["hipaa"],
            "description": "Connections to external/partner nodes (BAA partners) must use VPN or encrypted links to protect ePHI shared with business associates (HIPAA §164.314(a)).",
            "check": "baa_encrypted",
        },
        # ── IoT/OT Zone Security ─────────────────────────────────────────────────
        {
            "id": "NET-IOT-001",
            "title": "OT/ICS network isolated from IT",
            "severity": "CAT1",
            "category": "iot_ot",
            "regimes": ["iot_ot"],
            "description": "OT/ICS/SCADA devices must reside in a dedicated boundary with no direct edge to IT nodes without a firewall in the path (NIST 800-82 §5.3, IEC 62443 zone model).",
            "check": "ot_isolation",
        },
        {
            "id": "NET-IOT-002",
            "title": "Data diode or unidirectional gateway for OT egress",
            "severity": "CAT1",
            "category": "iot_ot",
            "regimes": ["iot_ot"],
            "description": "A data diode or unidirectional gateway node must exist between OT and IT networks to enforce one-way data flow from OT to IT (NIST 800-82 §5.3.1).",
            "check": "ot_data_diode",
        },
        {
            "id": "NET-IOT-003",
            "title": "IoT device management network separated",
            "severity": "CAT2",
            "category": "iot_ot",
            "regimes": ["iot_ot"],
            "description": "Management interfaces for IoT/OT devices must be on a separate network segment from production OT traffic (IEC 62443-3-3 SR 5.1).",
            "check": "iot_mgmt_separated",
        },
        {
            "id": "NET-IOT-004",
            "title": "OT network monitoring",
            "severity": "CAT2",
            "category": "iot_ot",
            "regimes": ["iot_ot"],
            "description": "IDS/IPS or SIEM must monitor the OT zone for anomalous traffic and potential intrusions (NIST 800-82 §6.2.1).",
            "check": "ot_monitoring",
        },
        # ── Kubernetes Network ────────────────────────────────────────────────────
        {
            "id": "NET-K8S-001",
            "title": "Default-deny network policy",
            "severity": "CAT1",
            "category": "k8s",
            "regimes": ["k8s"],
            "description": "Kubernetes cluster nodes must have network policy enforcement configured (default-deny ingress/egress). Label or config must indicate policy enforcement (CIS K8s 5.3.2).",
            "check": "k8s_network_policy",
        },
        {
            "id": "NET-K8S-002",
            "title": "Control plane isolated from data plane",
            "severity": "CAT1",
            "category": "k8s",
            "regimes": ["k8s"],
            "description": "The K8s API server / control plane must be in a separate boundary or segment from worker nodes to limit blast radius (CIS K8s 1.2.1).",
            "check": "k8s_cp_isolation",
        },
        {
            "id": "NET-K8S-003",
            "title": "Pod-to-pod encryption (mTLS/service mesh)",
            "severity": "CAT2",
            "category": "k8s",
            "regimes": ["k8s"],
            "description": "A service mesh (Istio, Linkerd) or mTLS must be configured between K8s pods for encrypted east-west traffic (NIST SC-8, ZTA).",
            "check": "k8s_mtls",
        },
        {
            "id": "NET-K8S-004",
            "title": "Ingress controller with WAF",
            "severity": "CAT2",
            "category": "k8s",
            "regimes": ["k8s"],
            "description": "The K8s ingress controller must have a WAF or firewall as a neighbor for application-layer protection (CIS K8s 5.3.1).",
            "check": "k8s_ingress_waf",
        },
        # ── DNS & BGP Security ────────────────────────────────────────────────────
        {
            "id": "NET-DNS-002",
            "title": "DNSSEC validation enabled",
            "severity": "CAT2",
            "category": "dns",
            "regimes": ["dns_bgp", "fisma_high"],
            "description": "DNS resolver nodes must have DNSSEC validation enabled to prevent DNS spoofing/cache poisoning (NIST 800-81 §8, SC-20).",
            "check": "dnssec_enabled",
        },
        {
            "id": "NET-DNS-003",
            "title": "Internal DNS resolvers (not public)",
            "severity": "CAT2",
            "category": "dns",
            "regimes": ["dns_bgp", "fisma_high"],
            "description": "DNS resolver nodes should not be directly connected to internet-facing nodes — use internal/split-horizon DNS (NIST 800-81 §9).",
            "check": "dns_internal",
        },
        {
            "id": "NET-BGP-001",
            "title": "BGP authentication (MD5/TCP-AO)",
            "severity": "CAT2",
            "category": "routing",
            "regimes": ["dns_bgp", "fisma_high", "stig"],
            "description": "BGP-protocol edges must have authentication enabled (MD5 or TCP-AO) to prevent route injection attacks (RFC 7454, NIST SC-23).",
            "check": "bgp_auth",
        },
        {
            "id": "NET-BGP-002",
            "title": "Route filtering configured",
            "severity": "CAT2",
            "category": "routing",
            "regimes": ["dns_bgp", "fisma_high", "stig"],
            "description": "Router nodes with BGP peering edges must have route filtering configured to prevent route leaks and hijacking (RFC 7454 §6).",
            "check": "bgp_route_filter",
        },
        # ── VDI / Virtual Desktop Infrastructure ──────────────────────────────────
        {
            "id": "NET-VDI-001",
            "title": "Session hosts on dedicated subnet",
            "severity": "CAT2",
            "category": "vdi_architecture",
            "regimes": ["fisma_high", "stig", "zta"],
            "description": "VDI session hosts must reside on a dedicated subnet, isolated from general-purpose servers (NIST SC-7, AC-4).",
            "check": "vdi_subnet_isolation",
        },
        {
            "id": "NET-VDI-002",
            "title": "VDI gateway at network boundary",
            "severity": "CAT1",
            "category": "vdi_architecture",
            "regimes": ["fisma_high", "stig", "zta", "cjis"],
            "description": "External VDI access must traverse a gateway (RD Gateway/Citrix Gateway/UAG) — no direct RDP to session hosts from untrusted zones (NIST SC-7, AC-17).",
            "check": "vdi_gateway_boundary",
        },
        {
            "id": "NET-VDI-003",
            "title": "VDI sessions use encrypted protocol",
            "severity": "CAT1",
            "category": "vdi_encryption",
            "regimes": ["fisma_high", "stig", "fips", "cjis", "icd503", "cnss1253"],
            "description": "All VDI session traffic (RDP/PCoIP/BLAST/ICA) must use TLS 1.2+ or FIPS-validated encryption (NIST SC-8, SC-13).",
            "check": "vdi_session_encryption",
        },
        {
            "id": "NET-VDI-004",
            "title": "Connection broker is redundant",
            "severity": "CAT2",
            "category": "vdi_availability",
            "regimes": ["fisma_high", "stig"],
            "description": "VDI connection broker must have HA/redundancy — single broker is a SPOF for all virtual desktops (NIST CP-7, SC-36).",
            "check": "vdi_broker_redundancy",
        },
        {
            "id": "NET-VDI-005",
            "title": "GPU hosts isolated from non-GPU workloads",
            "severity": "CAT2",
            "category": "vdi_architecture",
            "regimes": ["fisma_high", "stig"],
            "description": "GPU-enabled VDI hosts should be on a dedicated subnet or resource pool to prevent resource contention and enforce vGPU security profiles (NIST CM-7).",
            "check": "vdi_gpu_isolation",
        },
        {
            "id": "NET-VDI-006",
            "title": "Profile server uses encrypted storage",
            "severity": "CAT1",
            "category": "vdi_encryption",
            "regimes": ["fisma_high", "stig", "fips", "cjis"],
            "description": "FSLogix/UPM profile containers must reside on encrypted storage (Azure Files + SMB encryption or equivalent) (NIST SC-28).",
            "check": "vdi_profile_encryption",
        },
        {
            "id": "NET-VDI-007",
            "title": "No direct internet access from session hosts",
            "severity": "CAT1",
            "category": "vdi_architecture",
            "regimes": ["fisma_high", "stig", "zta", "cjis", "icd503"],
            "description": "VDI session hosts must not have direct internet egress — route through proxy/SASE/SWG (NIST SC-7, AC-4).",
            "check": "vdi_no_direct_internet",
        },
        {
            "id": "NET-VDI-008",
            "title": "Thin/zero clients use certificate-based auth",
            "severity": "CAT2",
            "category": "vdi_authentication",
            "regimes": ["fisma_high", "stig", "zta", "cjis"],
            "description": "Thin and zero client devices should use 802.1X or certificate-based device authentication before connecting to VDI infrastructure (NIST IA-3).",
            "check": "vdi_endpoint_cert_auth",
        },
    ]
    + WA_SECURITY_COMPLIANCE_RULES
    + MCSB_COMPLIANCE_RULES
    + GCP_SECURITY_COMPLIANCE_RULES
    + OCI_SECURITY_COMPLIANCE_RULES
    + IBM_SECURITY_COMPLIANCE_RULES
)

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


def run_compliance_audit(
    topology_id: str, graph: dict, regimes: list, classification: str = "CUI", has_as_built_version: bool = False
) -> dict:
    """Run all applicable compliance rules against a topology.

    Args:
        topology_id: UUID of the topology being audited.
        graph: Dict with "nodes" and "edges" lists.
        regimes: List of regime keys (e.g. ["fisma_high", "stig"]).
        classification: Classification level ("CUI", "SECRET", "TOP SECRET").
        has_as_built_version: Whether an as-built version exists in the DB
            (caller should query nc_versions before calling this).

    Returns:
        Dict with findings, scores per regime, and severity counts.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    {n["id"]: n for n in nodes}
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}

    # Build adjacency and node type sets
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])

    node_types = {n["id"]: n.get("type", "") for n in nodes}
    type_set = set(node_types.values())

    # Encryption-related types
    ENCRYPTOR_TYPES = {
        "fips-140-l1",
        "fips-140-l2",
        "fips-140-l3",
        "fips-140-l4",
        "hsm",
        "type1-encryptor",
        "kg-175d",
        "kg-175g",
        "kg-250",
        "kg-340",
        "kg-245x",
        "kg-255",
        "macsec",
    }
    TYPE1_TYPES = {"type1-encryptor", "kg-175d", "kg-175g", "kg-250", "kg-340", "kg-245x", "kg-255"}
    FIREWALL_TYPES = {"firewall", "aws-nfw", "az-fw", "gcp-armor", "oci-waf", "aws-waf", "az-nsg", "oci-nsg"}
    WAN_TYPES = {
        "cloud",
        "aws-dx",
        "aws-dx-gw",
        "az-er",
        "az-er-global",
        "gcp-ic",
        "oci-fc",
        "ibm-dl",
        "aws-vpn",
        "az-vpn-gw",
        "gcp-vpn",
        "ibm-vpn",
        "oci-vpn",
        "sdwan-overlay",
        "internet-exchange",
        "aws-direct-connect",
        "azure-expressroute",
        "gcp-interconnect",
        "oci-fastconnect",
    }
    CORE_TYPES = {"router", "switch-l3", "mpls-pe", "mpls-p", "route-reflector"}
    DIST_TYPES = {"switch-l3", "switch-l2"}
    ACCESS_TYPES = {"switch-l2", "wap"}
    CLOUD_TYPES = {t for t in type_set if t.startswith(("aws-", "az-", "gcp-", "oci-", "ibm-"))}
    DNS_TYPES = {"aws-r53", "az-dns", "gcp-dns"}
    MGMT_NODE_TYPES = {"siem", "network-tap", "wlc"}

    findings = []
    active_regimes = set(regimes)

    def add_finding(rule, affected="", affected_type="topology", fix_action=None):
        rule_regimes = set(rule["regimes"]) & active_regimes
        if not rule_regimes:
            return
        findings.append(
            {
                "rule_id": rule["id"],
                "title": rule["title"],
                "severity": rule["severity"],
                "category": rule["category"],
                "description": rule["description"],
                "regimes": sorted(rule_regimes),
                "affected_entity": affected,
                "affected_type": affected_type,
                "fix_action": fix_action,
            }
        )

    # Helper: check if path between two nodes passes through a device type
    def path_has_type(src, dst, target_types):
        visited = {src}
        queue = deque([(src, [src])])
        while queue:
            cur, path = queue.popleft()
            if cur == dst:
                return any(node_types.get(n, "") in target_types for n in path[1:-1])
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, path + [nb]))
        return False

    # ── Run each check ──────────────────────────────────────────────────
    rule_map = {r["id"]: r for r in COMPLIANCE_RULES}

    # NET-ENC-001: WAN links require encryption
    wan_nodes = [n for n in nodes if node_types.get(n["id"]) in WAN_TYPES]
    for wn in wan_nodes:
        neighbors = adj.get(wn["id"], set())
        has_encryptor = any(node_types.get(nb) in ENCRYPTOR_TYPES for nb in neighbors)
        encrypted_proto = any(
            e.get("protocol", "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")
            for e in edges
            if wn["id"] in (e["source"], e["target"])
        )
        if not has_encryptor and not encrypted_proto:
            add_finding(
                rule_map["NET-ENC-001"],
                label_map.get(wn["id"], wn["id"]),
                "node",
                {"action": "add_encryptor", "target_node": wn["id"], "encryptor_type": "fips-140-l2"},
            )

    # NET-ENC-002: Type 1 for SECRET+
    if classification in ("SECRET", "TOP SECRET"):
        for wn in wan_nodes:
            neighbors = adj.get(wn["id"], set())
            has_type1 = any(node_types.get(nb) in TYPE1_TYPES for nb in neighbors)
            if not has_type1:
                add_finding(
                    rule_map["NET-ENC-002"],
                    label_map.get(wn["id"], wn["id"]),
                    "node",
                    {"action": "add_encryptor", "target_node": wn["id"], "encryptor_type": "kg-175d"},
                )

    # NET-ENC-003: Encryptor speed match
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        if ntype in ENCRYPTOR_TYPES:
            max_rating = ENCRYPTOR_RATINGS.get(ntype, 0)
            for e in edges:
                if n["id"] in (e["source"], e["target"]):
                    label = (e.get("label") or "").upper()
                    link_mbps = 1000
                    if "400G" in label:
                        link_mbps = 400000
                    elif "100G" in label:
                        link_mbps = 100000
                    elif "40G" in label:
                        link_mbps = 40000
                    elif "25G" in label:
                        link_mbps = 25000
                    elif "10G" in label:
                        link_mbps = 10000
                    if link_mbps > max_rating and max_rating > 0:
                        add_finding(
                            rule_map["NET-ENC-003"],
                            f"{label_map.get(n['id'])} on {e.get('label', 'link')} ({link_mbps / 1000:.0f}G > {max_rating / 1000:.0f}G rated)",
                            "node",
                            {
                                "action": "upgrade_encryptor",
                                "target_node": n["id"],
                                "recommended": "kg-250" if link_mbps <= 100000 else "kg-340",
                            },
                        )

    # NET-ENC-004: FIPS validated crypto
    encrypted_edges = [
        e for e in edges if e.get("protocol", "").lower() in ("ipsec", "ipsec esp", "macsec", "tls", "gre/ipsec")
    ]
    for e in encrypted_edges:
        src_type = node_types.get(e["source"], "")
        tgt_type = node_types.get(e["target"], "")
        has_fips = (
            src_type in ENCRYPTOR_TYPES
            or tgt_type in ENCRYPTOR_TYPES
            or src_type.startswith("fips-")
            or tgt_type.startswith("fips-")
        )
        if not has_fips:
            add_finding(
                rule_map["NET-ENC-004"],
                f"{label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')}",
                "edge",
            )

    # NET-RED-001: Core/dist dual uplinks
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        nlabel = (n.get("label") or "").lower()
        is_core = ntype in CORE_TYPES or "core" in nlabel
        is_dist = ntype in DIST_TYPES and ("dist" in nlabel or "distribution" in nlabel)
        if is_core or is_dist:
            link_count = len(adj.get(n["id"], set()))
            if link_count < 2:
                add_finding(
                    rule_map["NET-RED-001"],
                    label_map.get(n["id"]),
                    "node",
                    {"action": "add_redundant_link", "target_node": n["id"]},
                )

    # NET-RED-002: Diverse paths (check if 2+ independent paths exist between core nodes)
    core_nodes = [
        n["id"] for n in nodes if node_types.get(n["id"]) in CORE_TYPES or "core" in (n.get("label") or "").lower()
    ]
    if len(core_nodes) >= 2:
        # Check if removing any single edge disconnects core nodes
        for e in edges:
            if e["source"] in core_nodes and e["target"] in core_nodes:
                # Test removing this edge
                test_adj = {}
                for e2 in edges:
                    if e2.get("id") == e.get("id"):
                        continue
                    test_adj.setdefault(e2["source"], set()).add(e2["target"])
                    test_adj.setdefault(e2["target"], set()).add(e2["source"])
                visited = {core_nodes[0]}
                queue = [core_nodes[0]]
                while queue:
                    cur = queue.pop(0)
                    for nb in test_adj.get(cur, set()):
                        if nb not in visited:
                            visited.add(nb)
                            queue.append(nb)
                if core_nodes[1] not in visited:
                    add_finding(
                        rule_map["NET-RED-002"],
                        f"Link {label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')} is single path between core nodes",
                        "edge",
                        {"action": "add_diverse_path", "source": e["source"], "target": e["target"]},
                    )
                    break  # one finding is enough

    # NET-RED-003: Access layer single uplink
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        nlabel = (n.get("label") or "").lower()
        if ntype in ACCESS_TYPES or "access" in nlabel:
            link_count = len(adj.get(n["id"], set()))
            if link_count <= 1:
                add_finding(rule_map["NET-RED-003"], label_map.get(n["id"]), "node")

    # NET-BND-001: Firewall at boundary
    has_firewall = bool(FIREWALL_TYPES & type_set)
    if not has_firewall:
        add_finding(
            rule_map["NET-BND-001"],
            "topology",
            "topology",
            {"action": "add_node", "node_type": "firewall", "label": "Perimeter FW"},
        )
    elif wan_nodes:
        # Check that firewall is between internal and WAN
        for wn in wan_nodes:
            neighbors = adj.get(wn["id"], set())
            fw_neighbor = any(node_types.get(nb) in FIREWALL_TYPES for nb in neighbors)
            if not fw_neighbor:
                add_finding(
                    rule_map["NET-BND-001"],
                    f"No firewall between {label_map.get(wn['id'])} and internal network",
                    "node",
                    {"action": "add_firewall_inline", "wan_node": wn["id"]},
                )

    # NET-BND-002: Micro-segmentation (ZTA requires multiple zones with FW between)
    fw_count = sum(1 for n in nodes if node_types.get(n["id"]) in FIREWALL_TYPES)
    if len(nodes) > 5 and fw_count < 2:
        add_finding(
            rule_map["NET-BND-002"],
            "topology",
            "topology",
            {"action": "add_node", "node_type": "firewall", "label": "Internal Segmentation FW"},
        )

    # NET-BND-003: Cloud isolation
    cloud_nodes = [n for n in nodes if node_types.get(n["id"]) in CLOUD_TYPES]
    onprem_nodes = [
        n for n in nodes if node_types.get(n["id"]) not in CLOUD_TYPES and node_types.get(n["id"]) not in WAN_TYPES
    ]
    if cloud_nodes and onprem_nodes:
        # Check that path from cloud to onprem goes through firewall
        for cn in cloud_nodes[:3]:
            for op in onprem_nodes[:3]:
                if not path_has_type(cn["id"], op["id"], FIREWALL_TYPES):
                    add_finding(
                        rule_map["NET-BND-003"],
                        f"{label_map.get(cn['id'])} to {label_map.get(op['id'])} — no firewall in path",
                        "node",
                    )
                    break
            else:
                continue
            break

    # NET-MGT-001: OOB management
    has_oob = any(
        "oob" in (n.get("label") or "").lower()
        or "management" in (n.get("label") or "").lower()
        or n.get("type") in MGMT_NODE_TYPES
        for n in nodes
    )
    if not has_oob:
        add_finding(rule_map["NET-MGT-001"], "topology", "topology")

    # NET-MGT-002: In-band management encrypted
    # Check for any telnet/http/snmpv1 protocols
    insecure_protos = [e for e in edges if e.get("protocol", "").lower() in ("telnet", "http", "snmpv1", "snmpv2")]
    for e in insecure_protos:
        add_finding(
            rule_map["NET-MGT-002"],
            f"{e.get('protocol')} on {label_map.get(e['source'], '?')} — {label_map.get(e['target'], '?')}",
            "edge",
        )

    # NET-DNS-001: DNS redundancy
    dns_nodes = [n for n in nodes if node_types.get(n["id"]) in DNS_TYPES]
    if len(dns_nodes) < 2 and len(nodes) > 3:
        add_finding(
            rule_map["NET-DNS-001"],
            "topology",
            "topology",
            {"action": "add_node", "node_type": "aws-r53", "label": "DNS (redundant)"},
        )

    # NET-ZTA-001: No implicit trust
    # If there are >5 nodes and no firewall/segmentation device in the path between any two segments
    if len(nodes) > 5 and len([n for n in nodes if node_types.get(n["id"]) in FIREWALL_TYPES]) == 0:
        add_finding(rule_map["NET-ZTA-001"], "topology", "topology")

    # NET-ZTA-002: East-west inspection
    firewalls = [n for n in nodes if node_types.get(n["id"]) in FIREWALL_TYPES]
    if len(nodes) > 8 and len(firewalls) <= 1:
        add_finding(
            rule_map["NET-ZTA-002"],
            "topology",
            "topology",
            {"action": "add_node", "node_type": "firewall", "label": "Internal FW (east-west)"},
        )

    # NET-CJIS-001: 128-bit minimum
    # Already covered by NET-ENC-001 effectively; add if encrypted but weak

    # NET-BP-001: All devices labeled
    unlabeled = [n for n in nodes if not n.get("label") or n.get("label", "").startswith("Untitled")]
    for un in unlabeled[:5]:
        add_finding(rule_map["NET-BP-001"], un.get("id", "?"), "node")

    # NET-BP-002: As-built version
    if not has_as_built_version:
        add_finding(
            rule_map["NET-BP-002"],
            "topology",
            "topology",
            {"action": "create_version", "label": "As-Built", "phase": "as-is"},
        )

    # ── Hybrid Cloud Networking Checks (Well-Architected Hybrid Networking Lens) ──
    DX_TYPES = {"aws-dx", "aws-dx-gw", "az-er", "az-er-global", "gcp-ic", "oci-fc", "ibm-dl"}
    VPN_BACKUP_TYPES = {"aws-vpn", "az-vpn-gw", "gcp-vpn", "ibm-vpn", "oci-vpn"}
    TRANSIT_HUB_TYPES = {"aws-tgw", "aws-cloudwan", "az-vwan", "gcp-ncc", "oci-drg", "ibm-tg"}
    VPC_TYPES = {"aws-vpc", "az-vnet", "gcp-vpc", "oci-vcn", "ibm-vpc"}
    FLOWLOG_TYPES = {"aws-flowlogs", "az-flowlogs", "gcp-flowlogs", "oci-flowlogs", "ibm-flowlogs"}
    PRIVATELINK_TYPES = {"aws-privatelink", "az-privatelink", "gcp-psc", "aws-gw-ep"}
    DDOS_TYPES = {"aws-shield", "az-ddos", "oci-ddos", "gcp-armor", "ibm-cis"}
    LB_TYPES = {
        "aws-alb",
        "aws-nlb",
        "aws-gwlb",
        "aws-ga",
        "az-appgw",
        "az-front",
        "az-crosslb",
        "gcp-lb",
        "gcp-gfe",
        "oci-lb",
        "ibm-lb",
    }
    INTERNET_FACING_TYPES = LB_TYPES | {"aws-cloudfront", "az-front", "gcp-cdn", "aws-waf"}

    dx_nodes_hyb = [n for n in nodes if node_types.get(n["id"]) in DX_TYPES]
    vpn_nodes_hyb = [n for n in nodes if node_types.get(n["id"]) in VPN_BACKUP_TYPES]
    vpc_nodes_hyb = [n for n in nodes if node_types.get(n["id"]) in VPC_TYPES]
    hub_nodes_hyb = [n for n in nodes if node_types.get(n["id"]) in TRANSIT_HUB_TYPES]

    # NET-HYB-001: BFD on DX VIFs
    for dn in dx_nodes_hyb:
        config = dn.get("config", {})
        if not config.get("bfd") and not config.get("bfd_enabled"):
            add_finding(
                rule_map.get("NET-HYB-001", {}),
                label_map.get(dn["id"], dn["id"]),
                "node",
                {"action": "set_config", "target_node": dn["id"], "key": "bfd_enabled", "value": True},
            )

    # NET-HYB-002: DX requires VPN backup
    if dx_nodes_hyb and not vpn_nodes_hyb:
        for dn in dx_nodes_hyb[:1]:
            csp_prefix = node_types.get(dn["id"], "")[:3]
            vpn_type_map = {"aws": "aws-vpn", "az-": "az-vpn-gw", "gcp": "gcp-vpn", "oci": "oci-vpn", "ibm": "ibm-vpn"}
            suggested_vpn = vpn_type_map.get(csp_prefix, "aws-vpn")
            add_finding(
                rule_map.get("NET-HYB-002", {}),
                label_map.get(dn["id"], dn["id"]),
                "node",
                {"action": "add_node", "node_type": suggested_vpn, "label": "VPN Backup"},
            )

    # NET-HYB-003: DX at 2+ diverse locations (only count real locations, not labels)
    if dx_nodes_hyb:
        locations = set()
        for dn in dx_nodes_hyb:
            config = dn.get("config", {})
            loc = config.get("location", "") or config.get("site", "")
            if loc:
                locations.add(loc.lower().strip())
        if len(locations) < 2:
            add_finding(
                rule_map.get("NET-HYB-003", {}),
                f"{len(dx_nodes_hyb)} DX at {len(locations)} known location(s) — need 2+ diverse sites",
                "topology",
            )

    # NET-HYB-004: Transit hub for multi-VPC
    if len(vpc_nodes_hyb) >= 2 and not hub_nodes_hyb:
        add_finding(
            rule_map.get("NET-HYB-004", {}),
            f"{len(vpc_nodes_hyb)} VPCs without transit hub",
            "topology",
            {"action": "add_node", "node_type": "aws-tgw", "label": "Transit Gateway"},
        )

    # NET-HYB-005: Flow logs on cloud VPCs
    flowlog_present = any(node_types.get(n["id"]) in FLOWLOG_TYPES for n in nodes)
    if vpc_nodes_hyb and not flowlog_present:
        vpc_labels = [label_map.get(v["id"], v["id"]) for v in vpc_nodes_hyb[:3]]
        for vl in vpc_labels:
            config = {}
            vpc_node = next((n for n in vpc_nodes_hyb if label_map.get(n["id"]) == vl), None)
            if vpc_node:
                config = vpc_node.get("config", {})
            if not config.get("flow_logs") and not config.get("flow_logs_enabled"):
                add_finding(
                    rule_map.get("NET-HYB-005", {}),
                    vl,
                    "node",
                    {"action": "set_config", "target_node": vl, "key": "flow_logs_enabled", "value": True},
                )
                break  # One finding is enough

    # NET-HYB-006: Private endpoints
    has_privatelink = any(node_types.get(n["id"]) in PRIVATELINK_TYPES for n in nodes)
    if vpc_nodes_hyb and not has_privatelink:
        add_finding(rule_map.get("NET-HYB-006", {}), "topology", "topology")

    # NET-HYB-007: DDoS protection
    has_ddos = any(node_types.get(n["id"]) in DDOS_TYPES for n in nodes)
    internet_facing = [n for n in nodes if node_types.get(n["id"]) in INTERNET_FACING_TYPES]
    if internet_facing and not has_ddos:
        affected = label_map.get(internet_facing[0]["id"], internet_facing[0]["id"])
        add_finding(
            rule_map.get("NET-HYB-007", {}),
            affected,
            "node",
            {"action": "add_node", "node_type": "aws-shield", "label": "DDoS Protection"},
        )

    # ── PCI-DSS Network Segmentation Checks ──────────────────────────────

    # Helper: identify CDE nodes by label keywords
    CDE_KEYWORDS = {"cde", "payment", "card", "cardholder", "pci"}
    cde_nodes = [n for n in nodes if any(kw in (n.get("label") or "").lower() for kw in CDE_KEYWORDS)]
    cde_ids = {n["id"] for n in cde_nodes}

    # NET-PCI-001: CDE isolated in dedicated boundary
    if "NET-PCI-001" in rule_map and cde_nodes:
        # CDE nodes should share a boundary/group or be exclusively connected
        non_cde_neighbors = set()
        for cid in cde_ids:
            for nb in adj.get(cid, set()):
                if nb not in cde_ids and node_types.get(nb) not in FIREWALL_TYPES:
                    non_cde_neighbors.add(nb)
        if non_cde_neighbors:
            add_finding(
                rule_map["NET-PCI-001"],
                f"CDE nodes directly connected to non-CDE: {', '.join(label_map.get(n, n) for n in list(non_cde_neighbors)[:3])}",
                "topology",
                {"action": "add_firewall_inline", "wan_node": list(non_cde_neighbors)[0]},
            )
    elif "NET-PCI-001" in rule_map and not cde_nodes and len(nodes) > 3:
        add_finding(rule_map["NET-PCI-001"], "No CDE-labeled nodes found — cannot verify CDE isolation", "topology")

    # NET-PCI-002: Firewall between CDE and untrusted networks
    if "NET-PCI-002" in rule_map and cde_nodes:
        wan_and_internet = [
            n for n in nodes if node_types.get(n["id"]) in WAN_TYPES or "internet" in (n.get("label") or "").lower()
        ]
        for wn in wan_and_internet[:3]:
            for cn in cde_nodes[:3]:
                if not path_has_type(wn["id"], cn["id"], FIREWALL_TYPES):
                    add_finding(
                        rule_map["NET-PCI-002"],
                        f"No firewall between {label_map.get(wn['id'])} and CDE node {label_map.get(cn['id'])}",
                        "node",
                        {"action": "add_firewall_inline", "wan_node": wn["id"]},
                    )
                    break
            else:
                continue
            break

    # NET-PCI-003: No direct internet access to CDE
    if "NET-PCI-003" in rule_map and cde_nodes:
        internet_nodes = [
            n
            for n in nodes
            if "internet" in (n.get("label") or "").lower() or node_types.get(n["id"]) in {"cloud", "internet-exchange"}
        ]
        for inet in internet_nodes:
            for cid in cde_ids:
                if cid in adj.get(inet["id"], set()):
                    add_finding(
                        rule_map["NET-PCI-003"],
                        f"Direct link from {label_map.get(inet['id'])} to CDE node {label_map.get(cid)}",
                        "edge",
                    )
                    break

    # NET-PCI-004: CDE monitoring (IDS/SIEM)
    MONITORING_TYPES = {"siem", "network-tap", "ids", "ips"}
    if "NET-PCI-004" in rule_map and cde_nodes:
        cde_has_monitor = False
        for cid in cde_ids:
            for nb in adj.get(cid, set()):
                if (
                    node_types.get(nb) in MONITORING_TYPES
                    or "siem" in (label_map.get(nb) or "").lower()
                    or "ids" in (label_map.get(nb) or "").lower()
                ):
                    cde_has_monitor = True
                    break
            if cde_has_monitor:
                break
        if not cde_has_monitor:
            add_finding(
                rule_map["NET-PCI-004"],
                "No IDS/SIEM connected to CDE zone",
                "topology",
                {"action": "add_node", "node_type": "siem", "label": "CDE SIEM/IDS"},
            )

    # NET-PCI-005: Wireless isolated from CDE
    if "NET-PCI-005" in rule_map and cde_nodes:
        wap_nodes = [n for n in nodes if node_types.get(n["id"]) == "wap"]
        for wap in wap_nodes:
            for cid in cde_ids:
                if cid in adj.get(wap["id"], set()):
                    add_finding(
                        rule_map["NET-PCI-005"],
                        f"WAP {label_map.get(wap['id'])} directly connected to CDE node {label_map.get(cid)}",
                        "edge",
                    )
                    break

    # NET-PCI-006: CDE access logging
    if "NET-PCI-006" in rule_map:
        has_siem = any(
            node_types.get(n["id"]) in MONITORING_TYPES
            or "siem" in (n.get("label") or "").lower()
            or "log" in (n.get("label") or "").lower()
            for n in nodes
        )
        if not has_siem:
            add_finding(
                rule_map["NET-PCI-006"],
                "No SIEM or log collector in topology",
                "topology",
                {"action": "add_node", "node_type": "siem", "label": "SIEM / Log Collector"},
            )

    # ── HIPAA PHI Network Checks ─────────────────────────────────────────

    PHI_KEYWORDS = {"phi", "ephi", "hipaa", "health", "patient", "medical", "ehr", "emr"}
    phi_nodes = [n for n in nodes if any(kw in (n.get("label") or "").lower() for kw in PHI_KEYWORDS)]
    phi_ids = {n["id"] for n in phi_nodes}

    # NET-HIPAA-001: PHI data flows encrypted in transit
    if "NET-HIPAA-001" in rule_map and phi_nodes:
        for e in edges:
            src, tgt = e["source"], e["target"]
            if src in phi_ids or tgt in phi_ids:
                proto = (e.get("protocol") or "").lower()
                is_encrypted = (
                    proto in ("ipsec", "ipsec esp", "macsec", "tls", "mtls", "gre/ipsec", "dtls")
                    or node_types.get(src) in ENCRYPTOR_TYPES
                    or node_types.get(tgt) in ENCRYPTOR_TYPES
                )
                if not is_encrypted:
                    add_finding(
                        rule_map["NET-HIPAA-001"],
                        f"Unencrypted link to PHI: {label_map.get(src)} — {label_map.get(tgt)}",
                        "edge",
                        {"action": "add_encryptor", "target_node": src, "encryptor_type": "fips-140-l2"},
                    )

    # NET-HIPAA-002: PHI zone access controlled (firewall/ACL)
    if "NET-HIPAA-002" in rule_map and phi_nodes:
        phi_has_fw = False
        for pid in phi_ids:
            for nb in adj.get(pid, set()):
                if nb not in phi_ids and node_types.get(nb) in FIREWALL_TYPES:
                    phi_has_fw = True
                    break
            if phi_has_fw:
                break
        if not phi_has_fw:
            add_finding(
                rule_map["NET-HIPAA-002"],
                "No firewall/ACL between PHI zone and other networks",
                "topology",
                {"action": "add_firewall_inline", "wan_node": list(phi_ids)[0] if phi_ids else ""},
            )

    # NET-HIPAA-003: PHI access audit logging
    if "NET-HIPAA-003" in rule_map and phi_nodes:
        phi_has_siem = False
        for pid in phi_ids:
            for nb in adj.get(pid, set()):
                if (
                    node_types.get(nb) in MONITORING_TYPES
                    or "siem" in (label_map.get(nb) or "").lower()
                    or "audit" in (label_map.get(nb) or "").lower()
                ):
                    phi_has_siem = True
                    break
            if phi_has_siem:
                break
        if not phi_has_siem:
            add_finding(
                rule_map["NET-HIPAA-003"],
                "No SIEM/audit logging connected to PHI zone",
                "topology",
                {"action": "add_node", "node_type": "siem", "label": "PHI Audit SIEM"},
            )

    # NET-HIPAA-004: BAA partner connections encrypted
    PARTNER_KEYWORDS = {"partner", "baa", "vendor", "external", "third-party", "3rd-party"}
    if "NET-HIPAA-004" in rule_map and phi_nodes:
        partner_nodes = [n for n in nodes if any(kw in (n.get("label") or "").lower() for kw in PARTNER_KEYWORDS)]
        for pn in partner_nodes:
            for pid in phi_ids:
                if pid in adj.get(pn["id"], set()):
                    e_match = next(
                        (
                            e
                            for e in edges
                            if (e["source"] == pn["id"] and e["target"] == pid)
                            or (e["source"] == pid and e["target"] == pn["id"])
                        ),
                        None,
                    )
                    if e_match:
                        proto = (e_match.get("protocol") or "").lower()
                        if proto not in ("ipsec", "ipsec esp", "tls", "mtls", "macsec", "gre/ipsec", "dtls"):
                            add_finding(
                                rule_map["NET-HIPAA-004"],
                                f"Unencrypted BAA link: {label_map.get(pn['id'])} — {label_map.get(pid)}",
                                "edge",
                                {"action": "add_encryptor", "target_node": pn["id"], "encryptor_type": "fips-140-l2"},
                            )

    # ── IoT/OT Zone Security Checks ──────────────────────────────────────

    OT_KEYWORDS = {"ot", "ics", "scada", "plc", "hmi", "rtu", "dcs", "sensor", "actuator"}
    IOT_KEYWORDS = {"iot", "sensor", "device", "edge-device", "gateway"}
    ot_nodes = [
        n
        for n in nodes
        if any(kw in (n.get("label") or "").lower() or kw in node_types.get(n["id"], "") for kw in OT_KEYWORDS)
    ]
    ot_ids = {n["id"] for n in ot_nodes}
    iot_nodes = [
        n
        for n in nodes
        if any(kw in (n.get("label") or "").lower() or kw in node_types.get(n["id"], "") for kw in IOT_KEYWORDS)
    ]

    DATA_DIODE_KEYWORDS = {"data-diode", "diode", "unidirectional", "one-way"}

    # NET-IOT-001: OT/ICS isolated from IT
    if "NET-IOT-001" in rule_map and ot_nodes:
        it_nodes_direct = set()
        for oid in ot_ids:
            for nb in adj.get(oid, set()):
                if nb not in ot_ids and node_types.get(nb) not in FIREWALL_TYPES:
                    # Check if neighbor is an IT-type node (not a firewall/diode)
                    nb_label = (label_map.get(nb) or "").lower()
                    nb_is_diode = any(kw in nb_label or kw in node_types.get(nb, "") for kw in DATA_DIODE_KEYWORDS)
                    if not nb_is_diode:
                        it_nodes_direct.add(nb)
        if it_nodes_direct:
            add_finding(
                rule_map["NET-IOT-001"],
                f"OT nodes directly connected to IT without firewall: {', '.join(label_map.get(n, n) for n in list(it_nodes_direct)[:3])}",
                "topology",
                {"action": "add_firewall_inline", "wan_node": list(it_nodes_direct)[0]},
            )

    # NET-IOT-002: Data diode for OT egress
    if "NET-IOT-002" in rule_map and ot_nodes:
        has_diode = any(
            any(kw in (n.get("label") or "").lower() or kw in node_types.get(n["id"], "") for kw in DATA_DIODE_KEYWORDS)
            for n in nodes
        )
        if not has_diode:
            add_finding(
                rule_map["NET-IOT-002"],
                "No data diode / unidirectional gateway between OT and IT",
                "topology",
                {"action": "add_node", "node_type": "data-diode", "label": "Data Diode (OT→IT)"},
            )

    # NET-IOT-003: IoT management network separated
    if "NET-IOT-003" in rule_map and (ot_nodes or iot_nodes):
        mgmt_segment = any(
            "management" in (n.get("label") or "").lower() or "mgmt" in (n.get("label") or "").lower() for n in nodes
        )
        if not mgmt_segment:
            add_finding(
                rule_map["NET-IOT-003"],
                "No dedicated management segment for IoT/OT devices",
                "topology",
                {"action": "add_node", "node_type": "switch-l3", "label": "OT Mgmt Switch"},
            )

    # NET-IOT-004: OT network monitoring
    if "NET-IOT-004" in rule_map and ot_nodes:
        ot_has_monitor = False
        for oid in ot_ids:
            for nb in adj.get(oid, set()):
                if (
                    node_types.get(nb) in MONITORING_TYPES
                    or "siem" in (label_map.get(nb) or "").lower()
                    or "ids" in (label_map.get(nb) or "").lower()
                ):
                    ot_has_monitor = True
                    break
            if ot_has_monitor:
                break
        if not ot_has_monitor:
            add_finding(
                rule_map["NET-IOT-004"],
                "No IDS/SIEM monitoring OT zone",
                "topology",
                {"action": "add_node", "node_type": "siem", "label": "OT IDS/SIEM"},
            )

    # ── Kubernetes Network Checks ────────────────────────────────────────

    K8S_KEYWORDS = {"k8s", "kubernetes", "kube", "eks", "aks", "gke", "openshift"}
    K8S_CP_KEYWORDS = {"api-server", "apiserver", "control-plane", "etcd", "kube-api", "master", "cp-"}
    K8S_WORKER_KEYWORDS = {"worker", "node-pool", "data-plane", "kubelet"}
    MESH_KEYWORDS = {"istio", "linkerd", "consul", "service-mesh", "envoy", "mtls"}
    INGRESS_KEYWORDS = {"ingress", "nginx-ingress", "traefik", "kong", "gateway-api"}
    WAF_TYPES = FIREWALL_TYPES | {"aws-waf", "gcp-armor", "oci-waf", "az-appgw"}

    k8s_nodes = [
        n
        for n in nodes
        if any(kw in (n.get("label") or "").lower() or kw in node_types.get(n["id"], "") for kw in K8S_KEYWORDS)
    ]
    k8s_ids = {n["id"] for n in k8s_nodes}

    # NET-K8S-001: Default-deny network policy
    if "NET-K8S-001" in rule_map and k8s_nodes:
        has_policy = any(
            n.get("config", {}).get("network_policy")
            or n.get("config", {}).get("default_deny")
            or "network-policy" in (n.get("label") or "").lower()
            or "netpol" in (n.get("label") or "").lower()
            for n in k8s_nodes
        )
        if not has_policy:
            add_finding(
                rule_map["NET-K8S-001"],
                "K8s cluster nodes lack network policy enforcement (default-deny)",
                "topology",
                {"action": "set_config", "target_node": k8s_nodes[0]["id"], "key": "default_deny", "value": True},
            )

    # NET-K8S-002: Control plane isolated from data plane
    if "NET-K8S-002" in rule_map and k8s_nodes:
        cp_nodes = [n for n in k8s_nodes if any(kw in (n.get("label") or "").lower() for kw in K8S_CP_KEYWORDS)]
        worker_nodes_k8s = [
            n for n in k8s_nodes if any(kw in (n.get("label") or "").lower() for kw in K8S_WORKER_KEYWORDS)
        ]
        if cp_nodes and worker_nodes_k8s:
            # Check firewall/segmentation between CP and workers
            for cp in cp_nodes[:1]:
                for wk in worker_nodes_k8s[:1]:
                    if not path_has_type(cp["id"], wk["id"], FIREWALL_TYPES):
                        add_finding(
                            rule_map["NET-K8S-002"],
                            f"No segmentation between {label_map.get(cp['id'])} and {label_map.get(wk['id'])}",
                            "topology",
                            {"action": "add_firewall_inline", "wan_node": cp["id"]},
                        )
        elif len(k8s_nodes) >= 2 and not cp_nodes:
            add_finding(
                rule_map["NET-K8S-002"],
                "K8s cluster found but no control plane node labeled — cannot verify isolation",
                "topology",
            )

    # NET-K8S-003: Pod-to-pod mTLS / service mesh
    if "NET-K8S-003" in rule_map and k8s_nodes:
        has_mesh = any(
            any(kw in (n.get("label") or "").lower() or kw in node_types.get(n["id"], "") for kw in MESH_KEYWORDS)
            for n in nodes
        )
        # Also check for mTLS protocol on k8s edges
        if not has_mesh:
            has_mtls_edge = any(
                e.get("protocol", "").lower() in ("mtls", "tls")
                for e in edges
                if e["source"] in k8s_ids or e["target"] in k8s_ids
            )
            if not has_mtls_edge:
                add_finding(
                    rule_map["NET-K8S-003"],
                    "No service mesh or mTLS between K8s pods",
                    "topology",
                    {"action": "add_node", "node_type": "service-mesh", "label": "Service Mesh (mTLS)"},
                )

    # NET-K8S-004: Ingress controller with WAF
    if "NET-K8S-004" in rule_map and k8s_nodes:
        ingress_nodes = [n for n in nodes if any(kw in (n.get("label") or "").lower() for kw in INGRESS_KEYWORDS)]
        if ingress_nodes:
            for ing in ingress_nodes:
                neighbors = adj.get(ing["id"], set())
                has_waf_neighbor = any(
                    node_types.get(nb) in WAF_TYPES or "waf" in (label_map.get(nb) or "").lower() for nb in neighbors
                )
                if not has_waf_neighbor:
                    add_finding(
                        rule_map["NET-K8S-004"],
                        f"Ingress {label_map.get(ing['id'])} has no WAF/firewall neighbor",
                        "node",
                        {"action": "add_node", "node_type": "aws-waf", "label": "Ingress WAF"},
                    )
        elif k8s_nodes:
            add_finding(rule_map["NET-K8S-004"], "No ingress controller found in K8s topology", "topology")

    # ── DNS & BGP Security Checks ────────────────────────────────────────

    ALL_DNS_TYPES = {"aws-r53", "az-dns", "gcp-dns"}
    dns_all = [n for n in nodes if node_types.get(n["id"]) in ALL_DNS_TYPES or "dns" in (n.get("label") or "").lower()]

    # NET-DNS-002: DNSSEC validation
    if "NET-DNS-002" in rule_map and dns_all:
        for dn in dns_all:
            config = dn.get("config", {})
            label_lower = (dn.get("label") or "").lower()
            has_dnssec = config.get("dnssec") or config.get("dnssec_enabled") or "dnssec" in label_lower
            if not has_dnssec:
                add_finding(
                    rule_map["NET-DNS-002"],
                    f"DNS node {label_map.get(dn['id'])} lacks DNSSEC validation",
                    "node",
                    {"action": "set_config", "target_node": dn["id"], "key": "dnssec_enabled", "value": True},
                )
                break  # One finding is enough

    # NET-DNS-003: Internal DNS resolvers (not public)
    if "NET-DNS-003" in rule_map and dns_all:
        internet_ids = {
            n["id"]
            for n in nodes
            if "internet" in (n.get("label") or "").lower() or node_types.get(n["id"]) in {"cloud", "internet-exchange"}
        }
        for dn in dns_all:
            neighbors = adj.get(dn["id"], set())
            if neighbors & internet_ids:
                add_finding(
                    rule_map["NET-DNS-003"],
                    f"DNS node {label_map.get(dn['id'])} directly connected to internet",
                    "node",
                )
                break

    # NET-BGP-001: BGP authentication
    if "NET-BGP-001" in rule_map:
        bgp_edges = [e for e in edges if (e.get("protocol") or "").lower() in ("bgp", "ebgp", "ibgp")]
        for be in bgp_edges:
            config = be.get("config", {})
            has_auth = config.get("auth") or config.get("md5") or config.get("tcp_ao") or config.get("authenticated")
            if not has_auth:
                add_finding(
                    rule_map["NET-BGP-001"],
                    f"BGP session {label_map.get(be['source'])} — {label_map.get(be['target'])} lacks authentication",
                    "edge",
                    {"action": "set_config", "target_node": be["source"], "key": "bgp_auth", "value": "md5"},
                )

    # NET-BGP-002: Route filtering
    if "NET-BGP-002" in rule_map:
        bgp_edges_rf = [e for e in edges if (e.get("protocol") or "").lower() in ("bgp", "ebgp", "ibgp")]
        bgp_router_ids = set()
        for be in bgp_edges_rf:
            bgp_router_ids.add(be["source"])
            bgp_router_ids.add(be["target"])
        for rid in bgp_router_ids:
            rn = next((n for n in nodes if n["id"] == rid), None)
            if rn:
                config = rn.get("config", {})
                has_filter = config.get("route_filter") or config.get("prefix_list") or config.get("route_filtering")
                if not has_filter:
                    add_finding(
                        rule_map["NET-BGP-002"],
                        f"Router {label_map.get(rid)} with BGP lacks route filtering",
                        "node",
                        {"action": "set_config", "target_node": rid, "key": "route_filtering", "value": True},
                    )
                    break  # One finding is enough

    # ── VDI / Virtual Desktop Infrastructure Checks ──────────────────────

    VDI_SESSION_HOST_TYPES = {
        "vdi-session-host",
        "avd-hostpool",
        "aws-workspaces",
        "aws-appstream",
        "gcp-vdi",
        "horizon-cloud",
    }
    VDI_BROKER_TYPES = {"vdi-connection-broker", "citrix-cloud"}
    VDI_GATEWAY_TYPES = {"vdi-gateway"}
    VDI_PROFILE_TYPES = {"vdi-profile-server"}
    VDI_GPU_TYPES = {"vdi-gpu-host"}
    VDI_ENDPOINT_TYPES = {"thin-client", "zero-client", "vdi-web-client"}
    VDI_ALL_TYPES = (
        VDI_SESSION_HOST_TYPES
        | VDI_BROKER_TYPES
        | VDI_GATEWAY_TYPES
        | VDI_PROFILE_TYPES
        | VDI_GPU_TYPES
        | VDI_ENDPOINT_TYPES
        | {"vdi-license-server", "vdi-image-store", "avd-workspace"}
    )

    vdi_nodes = [n for n in nodes if node_types.get(n["id"]) in VDI_ALL_TYPES]
    vdi_session_hosts = [n for n in nodes if node_types.get(n["id"]) in VDI_SESSION_HOST_TYPES]
    vdi_brokers = [n for n in nodes if node_types.get(n["id"]) in VDI_BROKER_TYPES]
    vdi_gateways = [n for n in nodes if node_types.get(n["id"]) in VDI_GATEWAY_TYPES]
    vdi_profiles = [n for n in nodes if node_types.get(n["id"]) in VDI_PROFILE_TYPES]
    vdi_gpu_hosts = [n for n in nodes if node_types.get(n["id"]) in VDI_GPU_TYPES]
    vdi_endpoints = [n for n in nodes if node_types.get(n["id"]) in VDI_ENDPOINT_TYPES]

    # NET-VDI-001: Session hosts on dedicated subnet
    if "NET-VDI-001" in rule_map and vdi_session_hosts:
        for sh in vdi_session_hosts:
            neighbors = adj.get(sh["id"], set())
            # Flag if session host is directly connected to a general-purpose server
            general_neighbors = [
                nb for nb in neighbors if node_types.get(nb) == "server" and nb not in {n["id"] for n in vdi_nodes}
            ]
            if general_neighbors:
                add_finding(
                    rule_map["NET-VDI-001"],
                    f"Session host {label_map.get(sh['id'])} shares subnet with general server(s): "
                    f"{', '.join(label_map.get(n, n) for n in general_neighbors[:3])}",
                    "node",
                    {"action": "add_node", "node_type": "az-subnet", "label": "VDI Session Host Subnet"},
                )
                break

    # NET-VDI-002: VDI gateway at network boundary
    if "NET-VDI-002" in rule_map and vdi_session_hosts:
        internet_ids = {
            n["id"]
            for n in nodes
            if "internet" in (n.get("label") or "").lower() or node_types.get(n["id"]) in {"cloud", "internet-exchange"}
        }
        if internet_ids and not vdi_gateways:
            add_finding(
                rule_map["NET-VDI-002"],
                "VDI session hosts present but no VDI gateway — external users may RDP directly",
                "topology",
                {"action": "add_node", "node_type": "vdi-gateway", "label": "VDI Gateway (RD GW / Citrix GW)"},
            )
        elif internet_ids and vdi_gateways:
            # Check that session hosts are NOT directly connected to internet
            for sh in vdi_session_hosts:
                neighbors = adj.get(sh["id"], set())
                if neighbors & internet_ids:
                    add_finding(
                        rule_map["NET-VDI-002"],
                        f"Session host {label_map.get(sh['id'])} directly connected to internet — must traverse gateway",
                        "node",
                        {"action": "add_firewall_inline", "wan_node": sh["id"]},
                    )
                    break

    # NET-VDI-003: VDI sessions use encrypted protocol
    if "NET-VDI-003" in rule_map and vdi_session_hosts:
        VDI_ENCRYPTED_PROTOS = {"tls", "rdp-tls", "pcoip", "blast", "ica-tls", "ipsec", "ipsec esp", "dtls", "wsp"}
        for sh in vdi_session_hosts:
            sh_edges = [e for e in edges if sh["id"] in (e["source"], e["target"])]
            has_encrypted = any((e.get("protocol") or "").lower() in VDI_ENCRYPTED_PROTOS for e in sh_edges)
            if not has_encrypted and sh_edges:
                add_finding(
                    rule_map["NET-VDI-003"],
                    f"Session host {label_map.get(sh['id'])} — no encrypted VDI protocol on connected edges",
                    "node",
                    {
                        "action": "set_config",
                        "target_node": sh["id"],
                        "key": "protocol_encryption",
                        "value": "TLS 1.2+",
                    },
                )
                break

    # NET-VDI-004: Connection broker redundancy
    if "NET-VDI-004" in rule_map and vdi_brokers:
        if len(vdi_brokers) < 2:
            add_finding(
                rule_map["NET-VDI-004"],
                f"Single connection broker ({label_map.get(vdi_brokers[0]['id'])}) — no HA/redundancy",
                "node",
                {"action": "add_node", "node_type": "vdi-connection-broker", "label": "Connection Broker (HA)"},
            )

    # NET-VDI-005: GPU hosts isolated
    if "NET-VDI-005" in rule_map and vdi_gpu_hosts:
        for gpu in vdi_gpu_hosts:
            neighbors = adj.get(gpu["id"], set())
            non_vdi_neighbors = [
                nb
                for nb in neighbors
                if node_types.get(nb) not in VDI_ALL_TYPES
                and node_types.get(nb) not in FIREWALL_TYPES
                and node_types.get(nb) == "server"
            ]
            if non_vdi_neighbors:
                add_finding(
                    rule_map["NET-VDI-005"],
                    f"GPU host {label_map.get(gpu['id'])} shares segment with non-GPU workloads",
                    "node",
                    {"action": "add_node", "node_type": "az-subnet", "label": "GPU Host Dedicated Subnet"},
                )
                break

    # NET-VDI-006: Profile server encrypted storage
    if "NET-VDI-006" in rule_map and vdi_profiles:
        for ps in vdi_profiles:
            config = ps.get("config", {})
            has_encryption = (
                config.get("encryption")
                or config.get("smb_encryption")
                or config.get("encrypted_storage")
                or "encrypt" in (ps.get("label") or "").lower()
            )
            if not has_encryption:
                add_finding(
                    rule_map["NET-VDI-006"],
                    f"Profile server {label_map.get(ps['id'])} — no encrypted storage configured",
                    "node",
                    {"action": "set_config", "target_node": ps["id"], "key": "encrypted_storage", "value": True},
                )
                break

    # NET-VDI-007: No direct internet from session hosts
    if "NET-VDI-007" in rule_map and vdi_session_hosts:
        internet_ids_vdi = {
            n["id"]
            for n in nodes
            if "internet" in (n.get("label") or "").lower() or node_types.get(n["id"]) in {"cloud", "internet-exchange"}
        }
        for sh in vdi_session_hosts:
            neighbors = adj.get(sh["id"], set())
            if neighbors & internet_ids_vdi:
                add_finding(
                    rule_map["NET-VDI-007"],
                    f"Session host {label_map.get(sh['id'])} has direct internet access — route through proxy/SASE",
                    "node",
                    {"action": "add_firewall_inline", "wan_node": sh["id"]},
                )
                break

    # NET-VDI-008: Thin/zero client cert auth
    if "NET-VDI-008" in rule_map and vdi_endpoints:
        for ep in vdi_endpoints:
            config = ep.get("config", {})
            has_cert = (
                config.get("cert_auth")
                or config.get("802.1x")
                or config.get("certificate_auth")
                or "802.1x" in (ep.get("label") or "").lower()
                or "cert" in (ep.get("label") or "").lower()
            )
            if not has_cert:
                add_finding(
                    rule_map["NET-VDI-008"],
                    f"Endpoint {label_map.get(ep['id'])} — no certificate-based authentication configured",
                    "node",
                    {"action": "set_config", "target_node": ep["id"], "key": "cert_auth", "value": True},
                )
                break

    # ── Score per regime ──────────────────────────────────────────────────
    total_rules_per_regime = {}
    failed_rules_per_regime = {}  # track unique rule IDs, not finding count
    for rule in COMPLIANCE_RULES:
        for r in rule["regimes"]:
            if r in active_regimes:
                total_rules_per_regime[r] = total_rules_per_regime.get(r, 0) + 1
    for f in findings:
        for r in f["regimes"]:
            failed_rules_per_regime.setdefault(r, set()).add(f["rule_id"])

    scores = {}
    for r in active_regimes:
        total = total_rules_per_regime.get(r, 1)
        failed = len(failed_rules_per_regime.get(r, set()))
        passed = max(0, total - failed)
        scores[r] = {
            "regime": COMPLIANCE_REGIMES.get(r, {}).get("name", r),
            "total_rules": total,
            "passed": passed,
            "failed": failed,
            "score_pct": round(passed / max(total, 1) * 100, 1),
        }

    cat1 = sum(1 for f in findings if f["severity"] == "CAT1")
    cat2 = sum(1 for f in findings if f["severity"] == "CAT2")
    cat3 = sum(1 for f in findings if f["severity"] == "CAT3")

    # ── Remediation plan ─────────────────────────────────────────────────
    remediation_plan = _build_remediation_plan(findings, scores, classification)

    # ── R-NAME: hostname naming-pattern violations ────────────────────────
    for n in nodes:
        hostname = n.get("hostname")
        naming_pattern = n.get("policy", {}).get("namingPattern")
        if not hostname or not naming_pattern:
            continue
        is_valid, suggested_name = validate_hostname_pattern(hostname, naming_pattern)
        if not is_valid:
            findings.append(
                {
                    "type": "R-NAME",
                    "node_id": n["id"],
                    "hostname": hostname,
                    "namingPattern": naming_pattern,
                    "autoFix": {"suggestedName": suggested_name},
                }
            )

    return {
        "topology_id": topology_id,
        "classification": classification,
        "regimes": regimes,
        "findings": findings,
        "scores": scores,
        "total_findings": len(findings),
        "cat1_count": cat1,
        "cat2_count": cat2,
        "cat3_count": cat3,
        "remediation_plan": remediation_plan,
    }


def _build_remediation_plan(findings: list, scores: dict, classification: str) -> dict:
    """Build a prioritized remediation plan from audit findings.

    Groups findings by severity, assigns priority phases, estimates effort,
    and generates actionable steps for each finding.

    Returns:
        Dict with phases, summary, estimated_effort, and overall_risk.
    """
    if not findings:
        return {
            "phases": [],
            "summary": "No findings — topology is fully compliant.",
            "estimated_effort": "None",
            "overall_risk": "LOW",
            "total_actions": 0,
        }

    # Effort estimates per fix action type (hours)
    effort_map = {
        "add_node": 2,
        "add_encryptor": 4,
        "add_firewall_inline": 4,
        "add_redundant_link": 3,
        "add_diverse_path": 6,
        "upgrade_encryptor": 8,
        "set_config": 1,
    }

    # Build prioritized phases
    phase_1 = []  # CAT1 — immediate (0-48h)
    phase_2 = []  # CAT2 — short-term (1-2 weeks)
    phase_3 = []  # CAT3 — planned (30 days)

    seen_rules = set()
    for f in findings:
        if f["rule_id"] in seen_rules:
            continue
        seen_rules.add(f["rule_id"])

        fix = f.get("fix_action")
        fix_type = fix.get("action") if fix else None
        effort_h = effort_map.get(fix_type, 2)

        action = {
            "rule_id": f["rule_id"],
            "title": f["title"],
            "severity": f["severity"],
            "description": f["description"],
            "affected": f.get("affected_entity", ""),
            "regimes": f.get("regimes", []),
            "effort_hours": effort_h,
            "auto_fixable": fix is not None,
            "fix_action": fix_type,
        }

        # Generate human-readable remediation step
        if fix_type == "add_node":
            node_label = fix.get("label", "security device")
            action["remediation_step"] = (
                f"Add {node_label} to the topology and connect to the appropriate network segment."
            )
        elif fix_type == "add_encryptor":
            action["remediation_step"] = (
                "Deploy a FIPS 140-2 validated encryptor on the affected link. Verify encryption grade matches classification level."
            )
        elif fix_type == "add_firewall_inline":
            action["remediation_step"] = (
                "Insert a firewall between the WAN/internet boundary and internal network. Configure stateful inspection rules and IDS/IPS policies."
            )
        elif fix_type == "add_redundant_link":
            action["remediation_step"] = (
                "Add a redundant uplink from the affected switch to a second distribution/core switch for failover."
            )
        elif fix_type == "add_diverse_path":
            action["remediation_step"] = (
                "Provision a physically diverse circuit path through a different conduit or provider for geographic redundancy."
            )
        elif fix_type == "upgrade_encryptor":
            action["remediation_step"] = (
                "Replace or upgrade the encryptor to meet the required encryption grade for the current classification level."
            )
        elif fix_type == "set_config":
            key = fix.get("key", "setting")
            val = fix.get("value", "enabled")
            action["remediation_step"] = f"Update configuration: set '{key}' to '{val}' on the affected device."  # nosec B608 - remediation message, not SQL
        else:
            action["remediation_step"] = f"Address finding: {f['description']}"

        if f["severity"] == "CAT1":
            phase_1.append(action)
        elif f["severity"] == "CAT2":
            phase_2.append(action)
        else:
            phase_3.append(action)

    phases = []
    if phase_1:
        phases.append(
            {
                "phase": 1,
                "name": "Immediate — Critical (0-48 hours)",
                "priority": "CRITICAL",
                "description": "CAT1 findings that deny or disrupt system capability. Must be resolved before ATO.",
                "actions": phase_1,
                "total_effort_hours": sum(a["effort_hours"] for a in phase_1),
                "auto_fixable_count": sum(1 for a in phase_1 if a["auto_fixable"]),
            }
        )
    if phase_2:
        phases.append(
            {
                "phase": 2,
                "name": "Short-Term — High (1-2 weeks)",
                "priority": "HIGH",
                "description": "CAT2 findings that degrade system capability. Should be resolved for full compliance.",
                "actions": phase_2,
                "total_effort_hours": sum(a["effort_hours"] for a in phase_2),
                "auto_fixable_count": sum(1 for a in phase_2 if a["auto_fixable"]),
            }
        )
    if phase_3:
        phases.append(
            {
                "phase": 3,
                "name": "Planned — Medium (30 days)",
                "priority": "MEDIUM",
                "description": "CAT3 findings — minor issues. Can be tracked in POAM for next review cycle.",
                "actions": phase_3,
                "total_effort_hours": sum(a["effort_hours"] for a in phase_3),
                "auto_fixable_count": sum(1 for a in phase_3 if a["auto_fixable"]),
            }
        )

    total_effort = sum(p["total_effort_hours"] for p in phases)
    total_actions = sum(len(p["actions"]) for p in phases)
    auto_fixable = sum(p["auto_fixable_count"] for p in phases)

    # Overall risk assessment
    lowest_score = min((s["score_pct"] for s in scores.values()), default=100)
    cat1_count = len(phase_1)
    if cat1_count >= 3 or lowest_score < 30:
        risk = "CRITICAL"
    elif cat1_count >= 1 or lowest_score < 50:
        risk = "HIGH"
    elif lowest_score < 70:
        risk = "MODERATE"
    else:
        risk = "LOW"

    # Summary text
    summary_parts = []
    if phase_1:
        summary_parts.append(
            f"{len(phase_1)} critical finding{'s' if len(phase_1) != 1 else ''} requiring immediate attention"
        )
    if phase_2:
        summary_parts.append(
            f"{len(phase_2)} high-priority finding{'s' if len(phase_2) != 1 else ''} for short-term resolution"
        )
    if phase_3:
        summary_parts.append(f"{len(phase_3)} medium finding{'s' if len(phase_3) != 1 else ''} for planned remediation")
    summary = ". ".join(summary_parts) + "."
    if auto_fixable:
        summary += f" {auto_fixable} of {total_actions} can be auto-fixed with one click."

    return {
        "phases": phases,
        "summary": summary,
        "estimated_effort": f"{total_effort} hours" if total_effort < 40 else f"{round(total_effort / 8)} days",
        "overall_risk": risk,
        "total_actions": total_actions,
        "auto_fixable": auto_fixable,
    }


def apply_compliance_fix(graph: dict, fix_action: dict) -> tuple:
    """Apply a one-click fix action to the graph for a specific finding.

    Args:
        graph: Dict with "nodes" and "edges" lists (will be mutated).
        fix_action: Dict with "action" key and action-specific params.

    Returns:
        Tuple of (applied: bool, detail: str).
        Note: "create_version" action is NOT handled here — it requires DB
        access and should be handled by the caller.
    """
    import uuid

    action = fix_action.get("action", "")
    applied = False
    detail = ""

    if action == "add_encryptor":
        # Add an encryption device between target node and its neighbor
        target = fix_action.get("target_node", "")
        enc_type = fix_action.get("encryptor_type", "fips-140-l2")
        target_node = next((n for n in graph["nodes"] if n["id"] == target), None)
        if target_node:
            enc_id = f"enc-{str(uuid.uuid4())[:8]}"
            graph["nodes"].append(
                {
                    "id": enc_id,
                    "label": f"Encryptor ({enc_type.upper()})",
                    "type": enc_type,
                    "x": target_node.get("x", 100) - 60,
                    "y": target_node.get("y", 100) - 40,
                }
            )
            # Link encryptor to target
            graph["edges"].append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "source": enc_id,
                    "target": target,
                    "label": "Encrypted",
                    "protocol": "IPSec",
                }
            )
            applied = True
            detail = f"Added {enc_type} encryptor next to {target_node.get('label', target)}"

    elif action == "add_node":
        ntype = fix_action.get("node_type", "firewall")
        label = fix_action.get("label", ntype)
        nid = f"fix-{str(uuid.uuid4())[:8]}"
        graph["nodes"].append(
            {
                "id": nid,
                "label": label,
                "type": ntype,
                "x": 300,
                "y": 50,
            }
        )
        applied = True
        detail = f"Added {label} ({ntype})"

    elif action == "add_redundant_link":
        target = fix_action.get("target_node", "")
        # Find a core/dist node not already connected
        target_neighbors = {e["target"] for e in graph["edges"] if e["source"] == target} | {
            e["source"] for e in graph["edges"] if e["target"] == target
        }
        core_candidates = [
            n
            for n in graph["nodes"]
            if n["id"] not in target_neighbors
            and n["id"] != target
            and (n.get("type") in ("router", "switch-l3") or "core" in (n.get("label") or "").lower())
        ]
        if core_candidates:
            peer = core_candidates[0]
            graph["edges"].append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "source": target,
                    "target": peer["id"],
                    "label": "Redundant",
                    "protocol": "",
                }
            )
            applied = True
            detail = f"Added redundant link from {next((n['label'] for n in graph['nodes'] if n['id'] == target), target)} to {peer['label']}"

    elif action == "add_firewall_inline":
        wan_node = fix_action.get("wan_node", "")
        wan = next((n for n in graph["nodes"] if n["id"] == wan_node), None)
        if wan:
            fw_id = f"fw-{str(uuid.uuid4())[:8]}"
            graph["nodes"].append(
                {
                    "id": fw_id,
                    "label": "Perimeter FW",
                    "type": "firewall",
                    "x": wan.get("x", 100) + 60,
                    "y": wan.get("y", 100),
                }
            )
            graph["edges"].append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "source": wan_node,
                    "target": fw_id,
                    "label": "",
                    "protocol": "",
                }
            )
            applied = True
            detail = f"Added firewall between {wan.get('label', wan_node)} and internal network"

    elif action == "upgrade_encryptor":
        target = fix_action.get("target_node", "")
        recommended = fix_action.get("recommended", "kg-250")
        target_node = next((n for n in graph["nodes"] if n["id"] == target), None)
        if target_node:
            target_node["type"] = recommended
            target_node["label"] = f"Encryptor ({recommended.upper()})"
            applied = True
            detail = f"Upgraded encryptor {target} to {recommended}"

    elif action == "add_diverse_path":
        source = fix_action.get("source", "")
        target = fix_action.get("target", "")
        if source and target:
            import uuid as _uuid

            graph["edges"].append(
                {
                    "id": str(_uuid.uuid4())[:8],
                    "source": source,
                    "target": target,
                    "label": "Diverse Path",
                    "protocol": "",
                }
            )
            applied = True
            detail = f"Added diverse path between {source} and {target}"

    elif action == "set_config":
        target = fix_action.get("target_node", "")
        key = fix_action.get("key", "")
        value = fix_action.get("value")
        target_node = next((n for n in graph["nodes"] if n["id"] == target), None)
        if target_node and key:
            config = target_node.setdefault("config", {})
            config[key] = value
            applied = True
            detail = f"Set {key}={value} on {target_node.get('label', target)}"

    return applied, detail


def generate_xacta_export(
    system_name: str, classification: str, environment: str, regimes_json: str, findings: list, now_str: str
) -> str:
    """Generate Xacta-compatible XML compliance report.

    Args:
        system_name: Name of the topology/system.
        classification: Classification level (e.g. "CUI").
        environment: Environment level (e.g. "IL4").
        regimes_json: JSON string of active regimes.
        findings: List of finding dicts from the DB.
        now_str: ISO timestamp string for the audit date.

    Returns:
        Xacta XML string.
    """
    findings_xml = []
    for f in findings:
        findings_xml.append(
            f'    <Finding id="{f["rule_id"]}" status="{f["status"]}">\n'
            f"      <Title>{f['title']}</Title>\n"
            f"      <Severity>{f['severity']}</Severity>\n"
            f"      <Regime>{f.get('regime', '')}</Regime>\n"
            f"      <Description>{f['description']}</Description>\n"
            f"      <AffectedEntity>{f.get('affected_entity', '')}</AffectedEntity>\n"
            f"      <Status>{f['status']}</Status>\n"
            f"      <CreatedAt>{f.get('created_at', '')}</CreatedAt>\n"
            f"      <RemediatedAt>{f.get('remediated_at', '') or ''}</RemediatedAt>\n"
            f"    </Finding>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<XactaComplianceReport xmlns="urn:xacta:compliance:1.0">\n'
        f"  <SystemName>{system_name}</SystemName>\n"
        f"  <Classification>{classification}</Classification>\n"
        f"  <Environment>{environment}</Environment>\n"
        f"  <Regimes>{regimes_json}</Regimes>\n"
        f"  <AuditDate>{now_str}</AuditDate>\n"
        f"  <TotalFindings>{len(findings)}</TotalFindings>\n"
        f"  <OpenFindings>{sum(1 for f in findings if f['status'] == 'open')}</OpenFindings>\n"
        f"  <RemediatedFindings>{sum(1 for f in findings if f['status'] == 'remediated')}</RemediatedFindings>\n"
        "  <Findings>\n" + "\n".join(findings_xml) + "\n"
        "  </Findings>\n"
        "</XactaComplianceReport>"
    )

    return xml


# ── FIPS 140 Encryption Coverage Report ───────────────────────────────────────

ENCRYPTION_DEVICE_TYPES = {
    "fips-140-l1",
    "fips-140-l2",
    "fips-140-l3",
    "fips-140-l4",
    "hsm",
    "type1-encryptor",
    "kg-175d",
    "kg-175g",
    "kg-250",
    "kg-340",
    "kg-245x",
    "kg-255",
    "macsec",
    "qkd-device",
    "tls-terminator",
}

ENCRYPTED_PROTOCOL_SET = {
    "ipsec",
    "ipsec esp",
    "gre/ipsec",
    "mtls",
    "tls",
    "dtls",
    "macsec",
    "bb84",
}


def generate_fips_coverage_report(
    system_name: str,
    classification: str,
    environment: str,
    graph: dict,
    now_str: str,
) -> dict:
    """Generate a FIPS 140 Encryption Coverage Report for ISSM review.

    Mirrors the client-side FIPS overlay logic but produces a structured,
    exportable report with coverage gaps, unprotected links, encryption
    device inventory, and actionable recommendations.

    Args:
        system_name: Name of the topology/system.
        classification: Classification level (CUI, SECRET, TOP SECRET).
        environment: Impact level (IL2-IL6).
        graph: Dict with "nodes" and "edges" lists.
        now_str: ISO timestamp for the report date.

    Returns:
        Dict with report sections: summary, encryption_devices,
        protected_links, unprotected_links, coverage_gaps, and
        recommendations.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    label_map = {n["id"]: n.get("label", n["id"]) for n in nodes}
    node_types = {n["id"]: n.get("type", "") for n in nodes}

    # ── Identify encryption devices ───────────────────────────────────
    encryption_devices = []
    enc_device_ids = set()
    for n in nodes:
        ntype = node_types.get(n["id"], "")
        if ntype in ENCRYPTION_DEVICE_TYPES:
            enc_device_ids.add(n["id"])
            rating = ENCRYPTOR_RATINGS.get(ntype, 0)
            encryption_devices.append(
                {
                    "id": n["id"],
                    "label": label_map.get(n["id"], n["id"]),
                    "type": ntype,
                    "fips_level": _fips_level(ntype),
                    "rated_throughput_mbps": rating,
                    "rated_throughput_display": _format_throughput(rating),
                }
            )

    # ── Build protected-node set (adjacent to encryption devices) ─────
    protected_node_ids = set(enc_device_ids)
    for e in edges:
        src, tgt = e.get("source", ""), e.get("target", "")
        if src in enc_device_ids:
            protected_node_ids.add(tgt)
        if tgt in enc_device_ids:
            protected_node_ids.add(src)

    # ── Classify each link ────────────────────────────────────────────
    protected_links = []
    unprotected_links = []

    for e in edges:
        src = e.get("source", "")
        tgt = e.get("target", "")
        proto = (e.get("protocol") or "").strip()
        link_label = e.get("label", "")

        has_encrypted_proto = proto.lower() in ENCRYPTED_PROTOCOL_SET
        src_is_enc = src in enc_device_ids
        tgt_is_enc = tgt in enc_device_ids
        src_protected = src in protected_node_ids
        tgt_protected = tgt in protected_node_ids

        is_protected = has_encrypted_proto or src_is_enc or tgt_is_enc or (src_protected and tgt_protected)

        link_info = {
            "id": e.get("id", ""),
            "source": label_map.get(src, src),
            "source_id": src,
            "target": label_map.get(tgt, tgt),
            "target_id": tgt,
            "label": link_label,
            "protocol": proto,
            "protection_method": _protection_method(
                has_encrypted_proto,
                src_is_enc,
                tgt_is_enc,
                src_protected,
                tgt_protected,
                proto,
                node_types,
                src,
                tgt,
                label_map,
            ),
        }

        if is_protected:
            protected_links.append(link_info)
        else:
            unprotected_links.append(link_info)

    # ── Coverage metrics ──────────────────────────────────────────────
    total_links = len(protected_links) + len(unprotected_links)
    coverage_pct = round(len(protected_links) / max(total_links, 1) * 100, 1)

    # ── Coverage gaps (nodes with unprotected connections) ────────────
    gap_nodes = {}
    for link in unprotected_links:
        for key in ("source_id", "target_id"):
            nid = link[key]
            ntype = node_types.get(nid, "")
            if ntype not in ENCRYPTION_DEVICE_TYPES:
                if nid not in gap_nodes:
                    gap_nodes[nid] = {
                        "id": nid,
                        "label": label_map.get(nid, nid),
                        "type": ntype,
                        "unprotected_link_count": 0,
                    }
                gap_nodes[nid]["unprotected_link_count"] += 1
    coverage_gaps = sorted(gap_nodes.values(), key=lambda g: g["unprotected_link_count"], reverse=True)

    # ── Recommendations ───────────────────────────────────────────────
    recommendations = _generate_recommendations(
        classification,
        environment,
        unprotected_links,
        coverage_gaps,
        encryption_devices,
        node_types,
        label_map,
        coverage_pct,
    )

    # ── Assemble report ───────────────────────────────────────────────
    risk_level = "LOW"
    if coverage_pct < 50:
        risk_level = "CRITICAL"
    elif coverage_pct < 75:
        risk_level = "HIGH"
    elif coverage_pct < 90:
        risk_level = "MODERATE"

    return {
        "report_type": "FIPS 140 Encryption Coverage Report",
        "system_name": system_name,
        "classification": classification,
        "environment": environment,
        "generated_at": now_str,
        "summary": {
            "total_links": total_links,
            "protected_links": len(protected_links),
            "unprotected_links": len(unprotected_links),
            "coverage_pct": coverage_pct,
            "risk_level": risk_level,
            "total_encryption_devices": len(encryption_devices),
            "total_nodes": len(nodes),
            "gap_node_count": len(coverage_gaps),
        },
        "encryption_devices": encryption_devices,
        "protected_links": protected_links,
        "unprotected_links": unprotected_links,
        "coverage_gaps": coverage_gaps,
        "recommendations": recommendations,
    }


def _fips_level(ntype: str) -> str:
    """Map device type to FIPS validation level."""
    level_map = {
        "fips-140-l1": "FIPS 140-2/3 Level 1",
        "fips-140-l2": "FIPS 140-2/3 Level 2",
        "fips-140-l3": "FIPS 140-2/3 Level 3",
        "fips-140-l4": "FIPS 140-2/3 Level 4",
        "hsm": "FIPS 140-2/3 Level 3 (HSM)",
        "type1-encryptor": "NSA Type 1",
        "kg-175d": "NSA Type 1 (KG-175D)",
        "kg-175g": "NSA Type 1 (KG-175G)",
        "kg-250": "NSA Type 1 (KG-250)",
        "kg-340": "NSA Type 1 (KG-340)",
        "kg-245x": "NSA Type 1 (KG-245X)",
        "kg-255": "NSA Type 1 (KG-255)",
        "macsec": "IEEE 802.1AE (MACsec)",
        "qkd-device": "Quantum Key Distribution",
        "tls-terminator": "TLS Termination Point",
    }
    return level_map.get(ntype, "Unknown")


def _format_throughput(mbps: int) -> str:
    """Format Mbps as human-readable string."""
    if mbps >= 1000000:
        return f"{mbps / 1000000:.0f} Tbps"
    if mbps >= 1000:
        return f"{mbps / 1000:.0f} Gbps"
    if mbps > 0:
        return f"{mbps} Mbps"
    return "N/A"


def _protection_method(
    has_proto: bool,
    src_enc: bool,
    tgt_enc: bool,
    src_prot: bool,
    tgt_prot: bool,
    proto: str,
    node_types: dict,
    src: str,
    tgt: str,
    label_map: dict,
) -> str:
    """Describe how a link is protected."""
    methods = []
    if has_proto:
        methods.append(f"Encrypted protocol ({proto})")
    if src_enc:
        methods.append(f"Source is encryption device ({label_map.get(src, src)})")
    if tgt_enc:
        methods.append(f"Target is encryption device ({label_map.get(tgt, tgt)})")
    if not has_proto and not src_enc and not tgt_enc and src_prot and tgt_prot:
        methods.append("Both endpoints adjacent to encryption devices")
    return "; ".join(methods) if methods else "None"


def _generate_recommendations(
    classification: str,
    environment: str,
    unprotected_links: list,
    coverage_gaps: list,
    encryption_devices: list,
    node_types: dict,
    label_map: dict,
    coverage_pct: float,
) -> list:
    """Generate actionable ISSM recommendations based on gaps."""
    recs = []
    priority = 1

    # Critical: classification requires Type 1
    if classification in ("SECRET", "TOP SECRET"):
        has_type1 = any(
            d["type"] in {"type1-encryptor", "kg-175d", "kg-175g", "kg-250", "kg-340", "kg-245x", "kg-255"}
            for d in encryption_devices
        )
        if not has_type1:
            recs.append(
                {
                    "priority": priority,
                    "severity": "CRITICAL",
                    "nist_control": "SC-13",
                    "title": "NSA Type 1 encryption required for classified data",
                    "detail": (
                        f"Classification level {classification} requires NSA Type 1 "
                        "encryption (e.g., KG-175D, KG-250) per CNSS Policy 15. "
                        "No Type 1 devices detected in topology."
                    ),
                    "action": "Deploy NSA Type 1 encryptors on all WAN/inter-site links.",
                }
            )
            priority += 1

    # High: unprotected WAN-facing links
    wan_types = {
        "cloud",
        "aws-dx",
        "az-er",
        "gcp-ic",
        "oci-fc",
        "ibm-dl",
        "aws-vpn",
        "az-vpn-gw",
        "gcp-vpn",
        "ibm-vpn",
        "sdwan-overlay",
        "internet-exchange",
    }
    wan_unprotected = [
        link
        for link in unprotected_links
        if node_types.get(link["source_id"], "") in wan_types or node_types.get(link["target_id"], "") in wan_types
    ]
    if wan_unprotected:
        link_list = ", ".join(f"{lnk['source']} \u2194 {lnk['target']}" for lnk in wan_unprotected[:5])
        recs.append(
            {
                "priority": priority,
                "severity": "CRITICAL",
                "nist_control": "SC-8, SC-13",
                "title": f"{len(wan_unprotected)} WAN/inter-site link(s) lack encryption",
                "detail": (
                    f"Unprotected WAN links: {link_list}"
                    + (f" (+{len(wan_unprotected) - 5} more)" if len(wan_unprotected) > 5 else "")
                    + ". Data in transit on these links is exposed."
                ),
                "action": (
                    "Deploy FIPS 140-2 Level 2+ validated encryptors (inline or "
                    "IPSec/MACsec) on each unprotected WAN link."
                ),
            }
        )
        priority += 1

    # Moderate: internal unprotected links
    internal_unprotected = [link for link in unprotected_links if link not in wan_unprotected]
    if internal_unprotected:
        recs.append(
            {
                "priority": priority,
                "severity": "HIGH" if coverage_pct < 75 else "MODERATE",
                "nist_control": "SC-8",
                "title": f"{len(internal_unprotected)} internal link(s) lack encryption",
                "detail": (
                    "Internal links without encryption may expose CUI "
                    "to lateral movement threats. "
                    + (f"Top gap nodes: {', '.join(g['label'] for g in coverage_gaps[:3])}." if coverage_gaps else "")
                ),
                "action": (
                    "Implement MACsec or IPSec on internal trunk links, prioritizing links between security zones."
                ),
            }
        )
        priority += 1

    # IL4+ requires FIPS validated crypto
    if environment in ("IL4", "IL5", "IL6"):
        non_fips_devices = [
            d
            for d in encryption_devices
            if not d["type"].startswith("fips-")
            and d["type"] != "hsm"
            and not d["type"].startswith("kg-")
            and d["type"] != "type1-encryptor"
        ]
        if non_fips_devices:
            recs.append(
                {
                    "priority": priority,
                    "severity": "HIGH",
                    "nist_control": "SC-13",
                    "title": "Non-FIPS-validated encryption devices detected",
                    "detail": (
                        f"{environment} requires FIPS 140-2/3 validated cryptographic "
                        f"modules. Devices without FIPS validation: "
                        + ", ".join(d["label"] for d in non_fips_devices)
                        + "."
                    ),
                    "action": (
                        "Replace or upgrade to FIPS 140-2/3 validated modules. "
                        "Verify CMVP certificate numbers for each device."
                    ),
                }
            )
            priority += 1

    # Coverage below threshold
    if coverage_pct < 100 and not recs:
        recs.append(
            {
                "priority": priority,
                "severity": "MODERATE",
                "nist_control": "SC-8, SC-13",
                "title": f"Encryption coverage at {coverage_pct}% — below 100% target",
                "detail": (
                    f"{len(unprotected_links)} link(s) remain unprotected. "
                    "Full encryption coverage is required for ATO."
                ),
                "action": "Review unprotected links and add encryption as appropriate.",
            }
        )
        priority += 1

    # Positive note if full coverage
    if coverage_pct == 100.0:
        recs.append(
            {
                "priority": priority,
                "severity": "INFO",
                "nist_control": "SC-8, SC-13",
                "title": "Full encryption coverage achieved",
                "detail": (
                    "All links are protected by FIPS-validated encryption or "
                    "encrypted protocols. Maintain coverage during future changes."
                ),
                "action": "No action required. Continue monitoring during topology changes.",
            }
        )

    return recs


def export_fips_report_html(report: dict) -> str:
    """Render a FIPS coverage report dict as a standalone HTML document.

    Suitable for ISSM review, printing, or attachment to ATO packages.
    Includes CUI markings, summary table, link inventory, and recommendations.
    """
    cls = report.get("classification", "CUI")
    banner = "CUI // SP-CTI" if cls == "CUI" else cls
    s = report["summary"]

    # Risk-level color
    risk_colors = {
        "CRITICAL": "#dc3545",
        "HIGH": "#fd7e14",
        "MODERATE": "#ffc107",
        "LOW": "#28a745",
        "INFO": "#17a2b8",
    }
    risk_color = risk_colors.get(s["risk_level"], "#6c757d")

    # Build encryption devices table rows
    dev_rows = ""
    for d in report["encryption_devices"]:
        dev_rows += (
            f"<tr><td>{_esc(d['label'])}</td><td>{_esc(d['type'])}</td>"
            f"<td>{_esc(d['fips_level'])}</td>"
            f"<td>{_esc(d['rated_throughput_display'])}</td></tr>\n"
        )
    if not dev_rows:
        dev_rows = '<tr><td colspan="4" style="color:#dc3545;font-weight:bold;">No encryption devices found</td></tr>'

    # Build unprotected links table rows
    unp_rows = ""
    for link in report["unprotected_links"]:
        unp_rows += (
            f"<tr><td>{_esc(link['source'])}</td>"
            f"<td>{_esc(link['target'])}</td>"
            f"<td>{_esc(link['label'])}</td>"
            f"<td>{_esc(link['protocol'] or 'None')}</td></tr>\n"
        )
    if not unp_rows:
        unp_rows = '<tr><td colspan="4" style="color:#28a745;">All links protected</td></tr>'

    # Build protected links table rows
    prot_rows = ""
    for link in report["protected_links"]:
        prot_rows += (
            f"<tr><td>{_esc(link['source'])}</td>"
            f"<td>{_esc(link['target'])}</td>"
            f"<td>{_esc(link['label'])}</td>"
            f"<td>{_esc(link['protection_method'])}</td></tr>\n"
        )
    if not prot_rows:
        prot_rows = '<tr><td colspan="4">No protected links</td></tr>'

    # Build coverage gaps table rows
    gap_rows = ""
    for g in report["coverage_gaps"]:
        gap_rows += (
            f"<tr><td>{_esc(g['label'])}</td><td>{_esc(g['type'])}</td><td>{g['unprotected_link_count']}</td></tr>\n"
        )
    if not gap_rows:
        gap_rows = '<tr><td colspan="3" style="color:#28a745;">No coverage gaps</td></tr>'

    # Build recommendations
    rec_html = ""
    for r in report["recommendations"]:
        sev_color = risk_colors.get(r["severity"], "#6c757d")
        rec_html += (
            f'<div style="border-left:4px solid {sev_color};padding:8px 12px;margin-bottom:10px;background:#1a1a2e;">'
            f"<strong>P{r['priority']} [{r['severity']}]</strong> — {_esc(r['title'])}<br>"
            f"<em>NIST: {_esc(r['nist_control'])}</em><br>"
            f"{_esc(r['detail'])}<br>"
            f"<strong>Action:</strong> {_esc(r['action'])}"
            f"</div>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>FIPS 140 Encryption Coverage Report — {_esc(report["system_name"])}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f0f23; color: #e0e0e0; margin: 0; padding: 20px; }}
  .banner {{ background: #b71c1c; color: #fff; text-align: center; padding: 6px; font-weight: bold; font-size: 14px; }}
  h1 {{ color: #00d4ff; margin-top: 20px; }}
  h2 {{ color: #00d4ff; border-bottom: 1px solid #333; padding-bottom: 4px; margin-top: 24px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
  th {{ background: #1a1a2e; color: #00d4ff; text-align: left; padding: 8px; border: 1px solid #333; }}
  td {{ padding: 8px; border: 1px solid #333; }}
  tr:nth-child(even) {{ background: #16213e; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
  .summary-card {{ background: #1a1a2e; border: 1px solid #333; border-radius: 6px; padding: 12px; text-align: center; }}
  .summary-card .value {{ font-size: 28px; font-weight: bold; }}
  .summary-card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .footer {{ margin-top: 30px; padding-top: 10px; border-top: 1px solid #333; font-size: 12px; color: #666; }}
  @media print {{
    body {{ background: #fff; color: #000; }}
    .banner {{ background: #b71c1c; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    th {{ background: #e0e0e0; color: #000; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
    tr:nth-child(even) {{ background: #f5f5f5; }}
    .summary-card {{ background: #f5f5f5; border-color: #ccc; }}
    h1, h2, th {{ color: #003366; }}
  }}
</style>
</head>
<body>
<div class="banner">{_esc(banner)}</div>
<h1>FIPS 140 Encryption Coverage Report</h1>
<p><strong>System:</strong> {_esc(report["system_name"])} &nbsp;|&nbsp;
   <strong>Classification:</strong> {_esc(cls)} &nbsp;|&nbsp;
   <strong>Environment:</strong> {_esc(report["environment"])} &nbsp;|&nbsp;
   <strong>Generated:</strong> {_esc(report["generated_at"])}</p>

<div class="summary-grid">
  <div class="summary-card">
    <div class="value" style="color:{risk_color};">{s["coverage_pct"]}%</div>
    <div class="label">Encryption Coverage</div>
  </div>
  <div class="summary-card">
    <div class="value" style="color:#28a745;">{s["protected_links"]}</div>
    <div class="label">Protected Links</div>
  </div>
  <div class="summary-card">
    <div class="value" style="color:#dc3545;">{s["unprotected_links"]}</div>
    <div class="label">Unprotected Links</div>
  </div>
  <div class="summary-card">
    <div class="value" style="color:{risk_color};">{s["risk_level"]}</div>
    <div class="label">Risk Level</div>
  </div>
</div>

<h2>Recommendations</h2>
{rec_html}

<h2>Encryption Device Inventory ({s["total_encryption_devices"]})</h2>
<table>
<tr><th>Device</th><th>Type</th><th>FIPS Level</th><th>Rated Throughput</th></tr>
{dev_rows}
</table>

<h2>Unprotected Links ({s["unprotected_links"]})</h2>
<table>
<tr><th>Source</th><th>Target</th><th>Link Label</th><th>Protocol</th></tr>
{unp_rows}
</table>

<h2>Coverage Gaps — Nodes with Unprotected Connections ({s["gap_node_count"]})</h2>
<table>
<tr><th>Node</th><th>Type</th><th>Unprotected Links</th></tr>
{gap_rows}
</table>

<h2>Protected Links ({s["protected_links"]})</h2>
<table>
<tr><th>Source</th><th>Target</th><th>Link Label</th><th>Protection Method</th></tr>
{prot_rows}
</table>

<div class="footer">
  <p>Generated by ICDEV&#8482; Network Design Canvas — FIPS 140 Encryption Coverage Audit</p>
  <p>This report is intended for ISSM review as part of the Authorization to Operate (ATO) package.</p>
</div>
<div class="banner">{_esc(banner)}</div>
</body>
</html>"""
    return html


def _esc(text: str) -> str:
    """Minimal HTML escaping for report output."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


import re as _re

_TEMPLATE_RE = _re.compile(
    r"^\{(?P<role>[^}]+)\}-\{(?P<site>[^}]+)\}-\{(?P<nn>[^}]+)\}$"
)
_TEMPLATE_LITERAL_RE = _re.compile(
    r"^(?P<role>[a-z][a-z0-9]*)-(?P<site>[a-z][a-z0-9]*)-(?P<nn>\d{2})$",
    _re.IGNORECASE,
)


def validate_hostname_pattern(hostname: str, pattern: str) -> tuple:
    """Return (is_valid, suggested_name) for hostname against pattern.

    pattern may be:
      • A template string like ``{role}-{site}-{nn}`` — hostname must have
        three hyphen-separated segments where the third segment is two digits.
      • Any other string is treated as a regex; hostname is matched in full.

    suggested_name is a corrected form when is_valid is False and a
    correction can be derived, otherwise None.
    """
    if not hostname or not pattern:
        return (False, None)

    if _TEMPLATE_RE.fullmatch(pattern):
        # Template pattern: expect <role>-<site>-<nn> (nn = 2-digit zero-padded)
        parts = hostname.split("-")
        if len(parts) != 3:
            suggested = None
            if len(parts) >= 3:
                role, site, *rest = parts
                nn_candidate = rest[-1]
                if nn_candidate.isdigit():
                    suggested = f"{role}-{site}-{int(nn_candidate):02d}"
            return (False, suggested)

        role, site, nn = parts
        if not role or not site:
            return (False, None)

        if not nn.isdigit():
            return (False, f"{role}-{site}-00")

        is_valid = True
        suggested = None
        # Suggest zero-padded form if nn is not exactly 2 digits
        if len(nn) != 2:
            suggested = f"{role}-{site}-{int(nn):02d}"
            is_valid = False
        return (is_valid, suggested)

    # Regex pattern
    try:
        compiled = _re.compile(pattern)
    except _re.error:
        return (False, None)

    is_valid = bool(compiled.fullmatch(hostname))
    return (is_valid, None)
