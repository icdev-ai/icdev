# CUI // SP-CTI — NDC Traffic Flow Engine
"""DoD BCAP path traffic flow analysis and walkthrough generator.

Provides DOMAIN_ACTION_MAP, PROTOCOL_TABLE, DOMAIN_DEFAULTS constants and
TrafficFlowEngine for creating flows and generating hop-by-hop walkthroughs
across the on_prem → NIPR → BCAP-VDMS → BCAP-VDSS → CSP IL4/IL5 path.
"""
from __future__ import annotations

import json
import uuid
from typing import Any


# ── Domain action maps ────────────────────────────────────────────────────────
# (domain_type, app_type) -> ordered list of action_type strings per hop.

DOMAIN_ACTION_MAP: dict[tuple[str, str], list[str]] = {
    # on_prem — user/endpoint originates traffic
    ("on_prem", "sso_saml"):     ["authenticate", "redirect"],
    ("on_prem", "sso_oauth"):    ["authorize", "redirect"],
    ("on_prem", "api_rest"):     ["request", "egress"],
    ("on_prem", "ipsec_tunnel"): ["encapsulate", "egress"],
    ("on_prem", "bgp"):          ["advertise", "egress"],
    ("on_prem", "dns"):          ["query", "resolve"],
    # nipr — DISA NIPRNet boundary: route + stateful inspection
    ("nipr", "sso_saml"):        ["route", "inspect", "forward"],
    ("nipr", "sso_oauth"):       ["route", "inspect", "forward"],
    ("nipr", "api_rest"):        ["route", "inspect", "forward"],
    ("nipr", "ipsec_tunnel"):    ["encrypt", "route", "forward"],
    ("nipr", "bgp"):             ["route-advertisement", "peering", "forward"],
    ("nipr", "dns"):             ["resolve", "route", "forward"],
    # bcap_vdms — BCAP DMZ Management Service: proxy + NAT + load-balance
    ("bcap_vdms", "sso_saml"):   ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "sso_oauth"):  ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "api_rest"):   ["proxy", "nat", "load-balance"],
    ("bcap_vdms", "ipsec_tunnel"): ["decrypt", "nat", "proxy"],
    ("bcap_vdms", "bgp"):        ["peer", "route-reflect", "forward"],
    ("bcap_vdms", "dns"):        ["resolve", "cache", "forward"],
    # bcap_vdss — BCAP DMZ Security Service: deep inspection + CDM
    ("bcap_vdss", "sso_saml"):   ["tls-inspect", "idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "sso_oauth"):  ["tls-inspect", "idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "api_rest"):   ["tls-inspect", "idps-scan", "waf-filter", "forward"],
    ("bcap_vdss", "ipsec_tunnel"): ["idps-scan", "cdm-check", "forward"],
    ("bcap_vdss", "bgp"):        ["inspect", "route-filter", "forward"],
    ("bcap_vdss", "dns"):        ["dns-inspect", "rpz-filter", "forward"],
    # csp_il4 — Cloud IL4: MFA + PKI + cloud-native firewall
    ("csp_il4", "sso_saml"):     ["mfa-verify", "pki-validate", "app-deliver"],
    ("csp_il4", "sso_oauth"):    ["mfa-verify", "token-issue", "app-deliver"],
    ("csp_il4", "api_rest"):     ["mfa-verify", "api-gateway", "app-deliver"],
    ("csp_il4", "ipsec_tunnel"): ["terminate", "decrypt", "app-deliver"],
    ("csp_il4", "bgp"):          ["peer", "route-import", "app-deliver"],
    ("csp_il4", "dns"):          ["resolve", "filter", "app-deliver"],
    # csp_il5 — Cloud IL5: same as IL4 + FIPS-140-2 gate
    ("csp_il5", "sso_saml"):     ["mfa-verify", "pki-validate", "fips-check", "app-deliver"],
    ("csp_il5", "sso_oauth"):    ["mfa-verify", "token-issue", "fips-check", "app-deliver"],
    ("csp_il5", "api_rest"):     ["mfa-verify", "api-gateway", "fips-check", "app-deliver"],
    ("csp_il5", "ipsec_tunnel"): ["terminate", "decrypt", "fips-check", "app-deliver"],
    ("csp_il5", "bgp"):          ["peer", "route-import", "fips-check", "app-deliver"],
    ("csp_il5", "dns"):          ["resolve", "filter", "fips-check", "app-deliver"],
    # jwics — JWICS SECRET network: Type 1 decrypt + HBSS + ACAS + NSS-PKI
    ("jwics", "sso_saml"):       ["type1-decrypt", "cac-authenticate", "nss-pki-validate", "app-deliver"],
    ("jwics", "sso_oauth"):      ["type1-decrypt", "cac-authenticate", "nss-pki-validate", "app-deliver"],
    ("jwics", "api_rest"):       ["type1-decrypt", "hbss-scan", "acas-check", "app-deliver"],
    ("jwics", "ipsec_tunnel"):   ["type1-decrypt", "route", "re-encrypt", "forward"],
    ("jwics", "bgp"):            ["ospf-neighbor", "route-advertise", "forward"],
    ("jwics", "dns"):            ["stub-resolve", "agency-recursive", "jwics-recursive", "dia-authoritative", "dnssec-validate", "response-cache"],
    ("jwics", "email"):          ["smime-sign", "smtp-relay", "hbss-content-scan", "dia-relay-deliver"],
    # c2s — AWS Secret Region (C2S): ClassifiedConnect + GuardDuty + CloudTrail
    ("c2s", "sso_saml"):         ["cac-authenticate", "iam-idc-validate", "fips-check", "app-deliver"],
    ("c2s", "sso_oauth"):        ["cac-authenticate", "iam-idc-validate", "fips-check", "app-deliver"],
    ("c2s", "api_rest"):         ["cac-authenticate", "api-gateway", "vpc-flowlog", "app-deliver"],
    ("c2s", "ipsec_tunnel"):     ["classifiedconnect-route", "tgw-attach", "vpc-deliver"],
    ("c2s", "bgp"):              ["classifiedconnect-bgp", "tgw-propagate", "forward"],
    ("c2s", "dns"):              ["route53-phz-resolve", "disa-dns-forward", "dnssec-validate"],
    ("c2s", "email"):            ["ses-internal-submit", "disa-gateway-route", "jwics-relay-deliver"],
    # c2e — Azure Government Secret (C2E): ExpressRoute + Defender + Private DNS
    ("c2e", "sso_saml"):         ["cac-authenticate", "entra-id-gov-validate", "fips-check", "app-deliver"],
    ("c2e", "sso_oauth"):        ["cac-authenticate", "entra-id-gov-validate", "fips-check", "app-deliver"],
    ("c2e", "api_rest"):         ["cac-authenticate", "apim-gateway", "azure-monitor-log", "app-deliver"],
    ("c2e", "ipsec_tunnel"):     ["expressroute-route", "vnet-gw-terminate", "vnet-deliver"],
    ("c2e", "bgp"):              ["expressroute-bgp", "vnet-route-propagate", "forward"],
    ("c2e", "dns"):              ["azure-private-dns-resolve", "disa-dns-forward", "dnssec-validate"],
    ("c2e", "email"):            ["exchange-online-gov-submit", "expressroute-dlp-check", "disa-gateway-route", "jwics-relay-deliver"],
}

# ── Protocol table ────────────────────────────────────────────────────────────
# Default ports/protocols/services per application type.

PROTOCOL_TABLE: dict[str, list[dict[str, Any]]] = {
    "sso_saml": [
        {"port": 443, "protocol": "tcp", "service": "HTTPS/TLS"},
        {"port": 80,  "protocol": "tcp", "service": "HTTP-redirect"},
    ],
    "sso_oauth": [
        {"port": 443, "protocol": "tcp", "service": "HTTPS/TLS"},
    ],
    "api_rest": [
        {"port": 443,  "protocol": "tcp", "service": "HTTPS/TLS"},
        {"port": 8443, "protocol": "tcp", "service": "HTTPS-alt"},
    ],
    "ipsec_tunnel": [
        {"port": 500,  "protocol": "udp", "service": "IKEv2"},
        {"port": 4500, "protocol": "udp", "service": "NAT-T"},
        {"protocol": "esp", "service": "ESP-encrypt"},
    ],
    "bgp": [
        {"port": 179, "protocol": "tcp", "service": "BGP"},
    ],
    "dns": [
        {"port": 53, "protocol": "udp", "service": "DNS"},
        {"port": 53, "protocol": "tcp", "service": "DNS-TCP"},
        {"port": 853, "protocol": "tcp", "service": "DNS-over-TLS"},
    ],
    "email": [
        {"port": 587, "protocol": "tcp", "service": "SMTP-STARTTLS (submission)"},
        {"port": 25,  "protocol": "tcp", "service": "SMTP (relay MTA-to-MTA)"},
        {"port": 993, "protocol": "tcp", "service": "IMAP-over-TLS (delivery)"},
        {"port": 636, "protocol": "tcp", "service": "LDAPS (cert/PKI lookup)"},
    ],
}

# ── Domain defaults ───────────────────────────────────────────────────────────
# Default security_detail and network_detail per domain_type.

DOMAIN_DEFAULTS: dict[str, dict[str, Any]] = {
    "on_prem": {
        "inspection_type": "endpoint-AV",
        "encryption_required": False,
    },
    "nipr": {
        "inspection_type": "stateful-firewall",
        "bgp_as": "AS65000-DISA",
        "route_source": "BGP",
        "encryption_required": True,
    },
    "bcap_vdms": {
        "inspection_type": "proxy",
        "dns_resolution": "DISA-DNS",
        "nat_translate": True,
        "load_balance_algo": "round-robin",
    },
    "bcap_vdss": {
        "inspection_type": "TLS-inspection + IDPS + CDM",
        "cdm_sensor": True,
        "idps_enabled": True,
        "tls_version": "1.3",
        "waF": True,
    },
    "csp_il4": {
        "inspection_type": "cloud-native-firewall",
        "encryption_required": True,
        "mfa_required": True,
        "pki_required": "DoD-PKI",
    },
    "csp_il5": {
        "inspection_type": "cloud-native-firewall + SIEM",
        "encryption_required": True,
        "mfa_required": True,
        "pki_required": "DoD-PKI",
        "fips_140_2": True,
    },
    "jwics": {
        "inspection_type": "Type 1 decrypt + HBSS malware scan + ACAS vuln check + SIEM",
        "encryption_required": True,
        "encryption_method": "NSA Type 1 (AES-256, HAIPE-compliant)",
        "mfa_required": True,
        "mfa_method": "CAC + PIN (NSS PKI)",
        "pki_required": "NSS (National Security System) CA",
        "fips_140_2": True,
        "classification": "SECRET",
        "dns_resolver": "JWICS recursive resolver (DIA-managed, not Internet-accessible)",
        "email_gateway": "JWICS SMTP relay — S/MIME mandatory, NSS PKI cert required",
        "routing_protocol": "OSPF Area 0 (JWICS backbone)",
        "physical_separation": "Physically isolated from NIPRNet; no shared infrastructure",
    },
    "c2s": {
        "inspection_type": "C2S VPC flow logs + AWS GuardDuty + CloudTrail + HBSS agent on EC2",
        "encryption_required": True,
        "encryption_method": "TLS 1.2+ FIPS 140-2 in-transit + NSA Type 1 on ClassifiedConnect circuit",
        "mfa_required": True,
        "mfa_method": "CAC + PIV + AWS IAM Identity Center (SSO)",
        "pki_required": "NSS CA + AWS Private CA (ACM-PCA in Secret Region)",
        "fips_140_2": True,
        "classification": "SECRET",
        "dns_resolver": "C2S Route 53 Private Hosted Zone (.c2s.ic.gov); DISA DNS via ClassifiedConnect for .smil.mil",
        "email_gateway": "SES internal endpoint in C2S (not public SES) → DISA Email Gateway → JWICS relay",
        "connectivity": "ClassifiedConnect (AWS Direct Connect variant) from DISA Secret CAP",
        "cloud_region": "us-gov-secret-1 (AWS Secret Region)",
    },
    "c2e": {
        "inspection_type": "Azure Firewall Premium IDPS + Defender for Cloud + Azure Monitor + HBSS agent on VMs",
        "encryption_required": True,
        "encryption_method": "TLS 1.2+ FIPS 140-2 in-transit + NSA Type 1 on ExpressRoute circuit",
        "mfa_required": True,
        "mfa_method": "CAC + PIV + Entra ID (Azure AD for Azure Government Secret)",
        "pki_required": "NSS CA + Azure Government PKI (Azure Key Vault HSM-backed)",
        "fips_140_2": True,
        "classification": "SECRET",
        "dns_resolver": "C2E Azure Private DNS Zone (.c2e.microsoft.com); conditional forwarder to DISA DNS for .smil.mil",
        "email_gateway": "Exchange Online for Government Secret → DLP policy → ExpressRoute → DISA Email Gateway → JWICS relay",
        "connectivity": "ExpressRoute from DISA Secret CAP to Azure Government Secret",
        "cloud_region": "Azure Government Secret (usgovsecret)",
    },
}

# ── Per-domain per-app narrative steps ───────────────────────────────────────
# Used by walkthrough generator to enrich hop descriptions with domain-specific detail.

FLOW_NARRATIVES: dict[tuple[str, str], list[str]] = {
    # DNS flows
    ("jwics", "dns"): [
        "SCIF workstation OS stub resolver issues query to the agency JWICS DNS resolver "
        "(DISA-IPAM-assigned address inside the SCIF network, e.g. 10.x.x.1:53). "
        "The resolver IP is distributed to all SCIF hosts via DHCP option 6 — no Internet DNS entries are configured.",
        "Agency JWICS DNS resolver (BIND 9 on DISA-hardened RHEL) checks its local cache. "
        "Cache hit returns immediately (default TTL 300 s). "
        "Cache miss triggers a forward to the DIA JWICS recursive resolver.",
        "Agency resolver forwards the query over the JWICS backbone to the DIA JWICS recursive resolver "
        "(DIA-managed, 10.x.x.x:53, not Internet-routable). "
        "The forwarder is configured with 'forward only' — the agency resolver does NOT perform its own iterative resolution.",
        "DIA JWICS recursive resolver checks its cache. On miss, it queries the DIA authoritative name server "
        "for the classified zones it owns: *.jwics.gov, *.dia.smil.mil, *.smil.mil, and any agency-delegated subzones. "
        "All queries stay within the JWICS backbone — no Internet path exists.",
        "DIA authoritative DNS returns the signed RRset (A/AAAA/PTR/MX). "
        "DNSSEC validation is performed at the DIA recursive resolver using the JWICS PKI trust anchor "
        "(not the Internet IANA root chain). "
        "The 'ad' (Authenticated Data) flag is set in the response when validation passes.",
        "DIA recursive resolver returns the validated response to the agency resolver, "
        "which caches the record (TTL from the authoritative zone, typically 300 s) "
        "and forwards the answer to the SCIF workstation stub resolver.",
        "Workstation application receives the resolved IP and opens the connection. "
        "The agency resolver logs the query to the DISA SIEM (syslog, AU-2 compliance). "
        "BLOCKED: Internet DNS (8.8.8.8, ISP resolvers) is unreachable from JWICS. "
        "NIPRNet resolvers are unreachable. Cross-domain lookups (JWICS → NIPR namespace) "
        "require an NSA-evaluated CDS DNS guard — they do not traverse the standard resolver chain.",
    ],
    ("c2s", "dns"): [
        "EC2 instance queries DHCP-assigned resolver — Route 53 Resolver at VPC+2 address (169.254.169.253).",
        "Route 53 Resolver evaluates Private Hosted Zone (PHZ) rules for .c2s.ic.gov zone; returns local record on hit.",
        "On miss: Resolver outbound endpoint forwards query to DISA-managed DNS via ClassifiedConnect circuit (not Internet path).",
        "Split-horizon: *.c2s.ic.gov → C2S PHZ; *.smil.mil → JWICS recursive resolver via Resolver Rule; *.mil catch-all → DISA DNS.",
        "DNSSEC validation enforced on all outbound Resolver Rules. Response returned to EC2. BLOCKED: Public Internet DNS (8.8.8.8) unreachable from C2S VNet.",
    ],
    ("c2e", "dns"): [
        "VM queries Azure recursive resolver at 168.63.129.16 — the VNet-level DNS server (not Internet).",
        "Azure Private DNS Zone is checked for .c2e.microsoft.com and any custom private zones registered in the subscription.",
        "Conditional DNS forwarder sends .smil.mil queries to DISA DNS via ExpressRoute circuit (DISA Secret CAP endpoint).",
        "Azure Private Resolver inbound endpoint receives DISA-side queries forwarded from JWICS for C2E-hosted resources.",
        "Response returned. BLOCKED: Public Azure DNS resolver (equivalent of 8.8.8.8) is unreachable from C2E VNet by design.",
    ],
    # Email flows
    ("jwics", "email"): [
        "User on SCIF workstation composes message in Outlook/Thunderbird; client auto-signs with NSS PKI S/MIME cert (DoD CA chain).",
        "MUA submits to agency SMTP relay on port 587 with STARTTLS; CAC + PIN authentication required.",
        "Agency SMTP relay resolves recipient MX record via JWICS DNS (no Internet DNS); routes *.dia.smil.mil to DIA JWICS relay.",
        "DIA JWICS relay performs HBSS content scan and keyword policy check; rejects classified spills or oversized attachments.",
        "DIA relay delivers to recipient agency mail server over SMTP/S; recipient MUA verifies S/MIME signature against NSS PKI.",
        "BLOCKED: No Internet relay path. Cross-domain to NIPRNet requires NSA-evaluated CDS email guard appliance.",
    ],
    ("c2s", "email"): [
        "Application in C2S VPC sends via SES SMTP endpoint (internal VPC endpoint — not public SES; region us-gov-secret-1).",
        "SES routes outbound mail to DISA Email Gateway in C2S via ClassifiedConnect (no Internet egress).",
        "DISA Gateway stamps classification header, applies DLP policy, routes to JWICS relay for .smil.mil recipients.",
        "Inbound path: JWICS relay → DISA Email Gateway in C2S → SES receive rule → S3 bucket or Lambda for processing.",
        "BLOCKED: Public-region SES (us-east-1, etc.) is not accessible from C2S. All mail must traverse DISA Email Gateway.",
    ],
    ("c2e", "email"): [
        "Application sends via Exchange Online for Government Secret SMTP relay endpoint (not public O365).",
        "Exchange Online applies DLP policy: blocks outbound to non-.mil/.gov domains; rejects unclassified domains by default.",
        "Exchange routes outbound via ExpressRoute to DISA Email Gateway (Secret CAP); DISA Gateway applies classification marking.",
        "DISA Gateway delivers to JWICS relay for .smil.mil recipients; SMTP TLS + S/MIME required end-to-end.",
        "BLOCKED: Exchange Online Government Secret cannot relay to public O365 or commercial SMTP servers — air-gapped by design.",
    ],
    # DNS flow variant: agency Infoblox NIOS → JWICS AWS Route 53 Resolver (C2S)
    ("jwics", "dns_infoblox_r53"): [
        "SCIF workstation OS stub resolver queries the Infoblox Grid Member VIP "
        "(DISA-IPAM-assigned address, pushed via DHCP option 6). "
        "Infoblox is configured with a dedicated JWICS DNS View — a logically separated namespace "
        "that holds only classified zone data and forwarder rules. The NIPR view is a separate view "
        "on the same Infoblox Grid; SCIF clients must not be able to query the NIPR view.",
        "Infoblox Grid Member evaluates the query against its JWICS view. "
        "A cache hit returns immediately (configurable TTL, default 300 s). "
        "On cache miss, Infoblox matches the query name against its Conditional Forwarder Zone list: "
        "(a) *.c2s.ic.gov → AWS R53 Resolver Inbound Endpoint IPs in the C2S VPC; "
        "(b) *.smil.mil, *.jwics.gov → DIA JWICS recursive resolver; "
        "(c) *.agency.jwics.gov → local Infoblox authoritative zone (served directly). "
        "No default forwarder pointing to Internet DNS is configured — any unmatched query returns NXDOMAIN.",
        "For queries matching *.c2s.ic.gov: Infoblox forwards to the AWS Route 53 Resolver "
        "Inbound Endpoint ENI IPs (e.g. 10.200.0.53, 10.200.1.53) via the ClassifiedConnect circuit. "
        "These ENIs are in dedicated resolver subnets within the C2S VPC. "
        "Infoblox must use 'Forward Only' mode for this forwarder zone to prevent fallback iteration.",
        "AWS Route 53 Resolver receives the query on the Inbound Endpoint. "
        "R53 Resolver evaluates Private Hosted Zone (PHZ) rules for the .c2s.ic.gov zone. "
        "A PHZ match returns the record directly. "
        "On PHZ miss, a Resolver Rule (FORWARD type) for *.smil.mil routes the query to DISA DNS "
        "via the ClassifiedConnect circuit (not the Internet). "
        "A second Resolver Rule (FORWARD type) for *.agency.jwics.gov routes back to the Infoblox "
        "Outbound Endpoint target IP — enabling C2S workloads to resolve on-prem JWICS names.",
        "DNSSEC validation: Infoblox NIOS validates DNSSEC responses using the JWICS trust anchor "
        "(configured under Grid > DNS > DNSSEC > Trust Anchors — add DIA DNSKEY, not IANA root key). "
        "R53 Resolver DNSSEC validation is configured on the Resolver outbound rules "
        "(DNSSEC validation mode: Enabled). Both layers must pass for the 'ad' flag to propagate.",
        "For the reverse path (C2S EC2 instance resolving an on-prem JWICS name): "
        "EC2 queries the VPC+2 resolver (169.254.169.253) → R53 Resolver evaluates rules → "
        "Resolver Rule (FORWARD, *.agency.jwics.gov) sends the query to the R53 Outbound Endpoint "
        "→ ClassifiedConnect → Infoblox Grid Member JWICS view → authoritative zone or "
        "forward to DIA JWICS recursive resolver. Response flows back the same path.",
        "All queries logged for AU-2 compliance: "
        "Infoblox query logs forwarded via syslog to DISA SIEM (rsyslog, TCP 514). "
        "R53 Resolver query logs sent to CloudWatch Logs or S3 in C2S. "
        "BLOCKED: Public Internet DNS (8.8.8.8) unreachable from both Infoblox (no Internet path from SCIF) "
        "and R53 Resolver (no Internet gateway in C2S VPC). "
        "Cross-domain queries (JWICS namespace → NIPR namespace) require an NSA-evaluated CDS DNS guard.",
    ],
    # BGP flows on JWICS/C2S/C2E
    ("jwics", "bgp"): [
        "Agency JWICS gateway establishes OSPF adjacency with DIA hub router over JWICS backbone (Area 0).",
        "Agency advertises its assigned JWICS subnet; DIA hub redistributes into JWICS backbone routing table.",
        "No BGP to Internet — JWICS uses OSPF internally; only DISA-managed inter-domain routing.",
    ],
    ("c2s", "bgp"): [
        "DISA Secret CAP establishes BGP session with AWS C2S TGW over ClassifiedConnect circuit (eBGP, MD5 auth).",
        "DISA advertises on-prem JWICS prefixes; C2S TGW propagates to all attached VPC route tables.",
        "C2S VPCs advertise VPC CIDRs back to DISA; routes appear in JWICS backbone via Secret CAP.",
    ],
    ("c2e", "bgp"): [
        "DISA Secret CAP establishes BGP session with Azure ExpressRoute gateway (eBGP, private peering, MD5 auth).",
        "DISA advertises JWICS prefixes; Azure VNet route table updated with on-prem routes automatically.",
        "C2E VNets advertise address space back via ExpressRoute; DISA CAP redistributes into JWICS routing.",
    ],
}

# ── Domain type resolver ──────────────────────────────────────────────────────

def resolve_domain_type(node: dict) -> str:
    """Infer domain_type from node properties (type, label, config).

    Returns one of: on_prem, nipr, bcap_vdms, bcap_vdss, csp_il4, csp_il5, jwics, c2s, c2e.
    Falls back to 'on_prem' when no pattern matches.
    """
    label = (node.get("label") or "").lower()
    ntype = (node.get("type") or "").lower()
    config = node.get("config") or {}
    config_str = json.dumps(config).lower() if isinstance(config, dict) else str(config).lower()

    # VDSS must be checked before BCAP/VDMS (more specific)
    if "vdss" in label or "vdss" in ntype:
        return "bcap_vdss"
    if "vdms" in label or "vdms" in ntype:
        return "bcap_vdms"
    if "bcap" in label or "bcap" in ntype:
        return "bcap_vdms"

    # IL5 before IL4 (more restrictive)
    if "il5" in label or "il5" in ntype or "il5" in config_str:
        return "csp_il5"
    if any(k in ntype or k in label for k in ("aws", "azure", "gcp", "oci")):
        return "csp_il4"
    if "csp" in label or "csp" in ntype or "cloud" in ntype:
        return "csp_il4"

    if "nipr" in label or "nipr" in ntype:
        return "nipr"

    # JWICS / C2S / C2E (must check before generic cloud)
    if any(k in label or k in ntype for k in ("jwics", "scif", "type1", "t1e", "jwx", "jwg")):
        return "jwics"
    if any(k in label or k in ntype for k in ("c2s", "classifiedconnect", "secret-region", "c2d", "c2t", "c2v", "c2z")):
        return "c2s"
    if any(k in label or k in ntype for k in ("c2e", "expressroute-secret", "azure-secret", "c2x", "c2n", "c2p")):
        return "c2e"
    if any(k in label or k in ntype for k in ("secret-bcap", "sbc", "secret-cap")):
        return "jwics"  # secret BCAP/CAP sits at the JWICS boundary

    if any(k in label or k in ntype for k in ("on.prem", "on_prem", "workstation", "endpoint", "user")):
        return "on_prem"

    return "on_prem"


# ── TrafficFlowEngine ─────────────────────────────────────────────────────────

class TrafficFlowEngine:
    """Create, list, delete traffic flows and generate DoD hop walkthroughs."""

    def _ensure_tables(self, conn) -> None:
        """Create nc_ traffic flow tables if not yet initialised by init_db."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_traffic_flows (
                id TEXT PRIMARY KEY,
                topology_id TEXT NOT NULL,
                name TEXT NOT NULL,
                src_zone TEXT NOT NULL,
                dst_zone TEXT NOT NULL,
                app_type TEXT NOT NULL,
                classification TEXT DEFAULT 'CUI',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_security_domain_policies (
                id TEXT PRIMARY KEY,
                topology_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                domain_type TEXT NOT NULL,
                inspection_type TEXT,
                policy_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nc_flow_walkthrough_steps (
                id TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                node_id TEXT NOT NULL,
                node_label TEXT NOT NULL,
                action_type TEXT NOT NULL,
                security_detail TEXT DEFAULT '{}',
                network_detail TEXT DEFAULT '{}',
                narrative TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_flow(
        self,
        topology_id: str,
        name: str,
        src_zone: str,
        dst_zone: str,
        app_type: str,
        classification: str,
        conn,
    ) -> str:
        """Insert a new traffic flow record and return its UUID."""
        self._ensure_tables(conn)
        flow_id = str(uuid.uuid4())
        conn.execute(
            # src_zone/dst_zone/app_type are source_zone/destination_zone/
            # application_type on the live table (swp-scan-01).
            """INSERT INTO nc_traffic_flows
               (id, topology_id, name, source_zone, destination_zone, application_type, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (flow_id, topology_id, name, src_zone, dst_zone, app_type, classification),
        )
        conn.commit()
        return flow_id

    def list_flows(self, topology_id: str, conn) -> list[dict]:
        """Return all flows for a topology as plain dicts."""
        self._ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM nc_traffic_flows WHERE topology_id = %s ORDER BY created_at",
            (topology_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_flow(self, flow_id: str, conn) -> bool:
        """Delete a flow and its walkthrough steps. Returns True if a row was deleted."""
        self._ensure_tables(conn)
        conn.execute(
            "DELETE FROM nc_flow_walkthrough_steps WHERE flow_id = %s", (flow_id,)
        )
        cur = conn.execute(
            "DELETE FROM nc_traffic_flows WHERE id = %s", (flow_id,)
        )
        conn.commit()
        deleted = getattr(cur, "rowcount", None)
        return bool(deleted and deleted > 0)

    # ── Walkthrough generation ────────────────────────────────────────────────

    def generate_walkthrough(self, flow_id: str, conn, phase_id: str | None = None) -> list[dict]:
        """Build hop-by-hop walkthrough for a flow and persist it.

        Steps:
          1. Load flow from nc_traffic_flows
          2. Load topology graph_json; if phase_id given, project topology to that phase state
          3. BFS path via path_analyzer.find_paths()
          4. For each hop: resolve domain_type, look up domain policy (DB then DOMAIN_DEFAULTS)
          5. Build step dict, persist to nc_flow_walkthrough_steps
          6. Return steps list
        """
        from tools.network.path_analyzer import find_paths

        self._ensure_tables(conn)

        flow_row = conn.execute(
            "SELECT * FROM nc_traffic_flows WHERE id = %s", (flow_id,)
        ).fetchone()
        if not flow_row:
            return []
        flow = dict(flow_row)

        topo_row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s", (flow["topology_id"],)
        ).fetchone()

        nodes_dict: dict[str, dict] = {}
        if topo_row and topo_row["graph_json"]:
            raw_graph = json.loads(topo_row["graph_json"])

            # If phase_id provided, project the topology to that phase's post-state
            if phase_id:
                try:
                    from tools.network.migration_phases import generate_phase_graph
                    phase_row = conn.execute(
                        "SELECT * FROM nc_migration_phases WHERE id=%s", (phase_id,)
                    ).fetchone()
                    if phase_row:
                        raw_graph = generate_phase_graph(raw_graph, dict(phase_row))
                except Exception:
                    pass  # fall back to current topology if projection fails

            nodes_list: list[dict] = raw_graph.get("nodes", [])
            edges: list[dict] = raw_graph.get("edges", [])
            nodes_dict = {n["id"]: n for n in nodes_list}
            graph = {"nodes": nodes_dict, "edges": edges}
            result = find_paths(flow["src_zone"], flow["dst_zone"], graph)
            hops: list[str] = []
            if result.get("paths"):
                open_paths = [p for p in result["paths"] if not p.get("acl_blocked")]
                chosen = open_paths[0] if open_paths else result["paths"][0]
                hops = chosen.get("hops", [])
        else:
            hops = []

        if not hops:
            # No path found (or no topology) — still emit src/dst as two steps
            hops = [flow["src_zone"], flow["dst_zone"]]

        app_type: str = flow["app_type"]
        protocols = PROTOCOL_TABLE.get(app_type, [])

        # Load per-node domain policy overrides from DB (keyed by node_id)
        policy_rows = conn.execute(
            "SELECT * FROM nc_security_domain_policies WHERE topology_id = %s",
            (flow["topology_id"],),
        ).fetchall()
        policy_by_node: dict[str, dict] = {r["node_id"]: dict(r) for r in policy_rows}

        # Delete stale steps before regenerating
        conn.execute(
            "DELETE FROM nc_flow_walkthrough_steps WHERE flow_id = %s", (flow_id,)
        )

        steps: list[dict] = []
        for step_number, node_id in enumerate(hops, start=1):
            node = nodes_dict.get(node_id, {"id": node_id, "label": node_id, "type": ""})
            node_label = node.get("label") or node_id
            domain_type = resolve_domain_type(node)

            # Security detail: DB override > DOMAIN_DEFAULTS
            if node_id in policy_by_node:
                db_policy = policy_by_node[node_id]
                policy_json = json.loads(db_policy.get("policy_json") or "{}")
                security_detail: dict = {
                    "domain_type": db_policy.get("domain_type", domain_type),
                    "inspection_type": db_policy.get("inspection_type") or "",
                    **policy_json,
                }
            else:
                security_detail = dict(DOMAIN_DEFAULTS.get(domain_type, {}))
                security_detail["domain_type"] = domain_type

            # Network detail: protocol table entries for this app_type
            network_detail: dict = {
                "protocols": protocols,
                "app_type": app_type,
            }

            # Primary action for this hop
            action_list = DOMAIN_ACTION_MAP.get((domain_type, app_type), [])
            action_type = action_list[0] if action_list else "forward"

            step: dict = {
                "step_number": step_number,
                "node_id": node_id,
                "node_label": node_label,
                "action_type": action_type,
                "security_detail": security_detail,
                "network_detail": network_detail,
                "narrative": "",
            }
            steps.append(step)

            conn.execute(
                """INSERT INTO nc_flow_walkthrough_steps
                   (id, flow_id, step_number, node_id, node_label,
                    action_type, security_detail, network_detail, narrative)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(uuid.uuid4()),
                    flow_id,
                    step_number,
                    node_id,
                    node_label,
                    action_type,
                    json.dumps(security_detail),
                    json.dumps(network_detail),
                    "",
                ),
            )

        conn.commit()
        return steps

    def get_walkthrough(self, flow_id: str, conn) -> list[dict]:
        """Return persisted walkthrough steps for a flow."""
        self._ensure_tables(conn)
        rows = conn.execute(
            """SELECT step_number, node_id, node_label, action_type,
                      security_detail, network_detail, narrative
               FROM nc_flow_walkthrough_steps
               WHERE flow_id = %s
               ORDER BY step_number""",
            (flow_id,),
        ).fetchall()
        steps = []
        for r in rows:
            step = dict(r)
            for field in ("security_detail", "network_detail"):
                val = step.get(field)
                if isinstance(val, str):
                    try:
                        step[field] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        step[field] = {}
            steps.append(step)
        return steps
