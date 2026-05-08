# CUI // SP-CTI
"""NDC SOP Seeder — seeds cloud connectivity SOPs into ndc_sops table.

Usage:
    python tools/network/seed_sops.py [--dry-run] [--json] [--category <cat>] [--status approved]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from tools.network.db.init_db import init_db
from tools.network.sops import create_sop, list_sops, submit_for_review, approve_sop

# ── Category 1: Physical Connectivity ─────────────────────────────────────────

_PHYSICAL = [
    {
        "title": "AWS Direct Connect — Dedicated Connection Provisioning",
        "category": "physical_connectivity",
        "csp": "aws",
        "description": "Provision a dedicated AWS Direct Connect connection at 1G/10G/100G. Covers LOA-CFA issuance, cross-connect at colocation, virtual interface creation, and BGP session establishment for DoD IL4/IL5 workloads.",
        "prerequisites": ["AWS account with DX service access", "Colocation cage or meet-me-room at DX location", "ASN assigned (public or private)", "DISA circuit request if NIPRNet on-ramp"],
        "steps": [
            {
                "number": 1, "title": "Request Dedicated Connection",
                "platform": "aws-console",
                "prerequisites": ["IAM permission: directconnect:CreateConnection"],
                "actions": [{"type": "cli", "label": "Create DX connection", "command": "aws directconnect create-connection --bandwidth 10Gbps --connection-name dx-prod-il4 --location EQUINIX-DC2 --provider-name Equinix --region us-gov-east-1", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws directconnect describe-connections --region us-gov-east-1 --query 'connections[?connectionName==`dx-prod-il4`].connectionState'", "expected_output": "[\"requested\"]"}],
                "expected_result": "Connection in 'requested' state; connection ID returned",
                "rollback_commands": [{"command": "aws directconnect delete-connection --connection-id <conn-id>", "label": "Delete pending connection"}],
                "notes": ["DOD REQUIREMENT: Use GovCloud (us-gov-east-1 or us-gov-west-1) for IL4+", "LOA-CFA issued within 72h; download from DX console"],
                "time_est": "15m", "dod_ref": "SCCA FRD §3.2.1"
            },
            {
                "number": 2, "title": "Download LOA-CFA and Submit Cross-Connect",
                "platform": "aws-console",
                "prerequisites": ["Connection in 'requested' state"],
                "actions": [{"type": "console", "label": "Download LOA", "command": "DX Console → Connection → Download LOA-CFA → Submit to colo provider", "cli_type": "console"}],
                "verification": [{"command": "aws directconnect describe-connections --connection-id <conn-id> --query 'connections[0].connectionState'", "expected_output": "\"available\""}],
                "expected_result": "Physical layer up; connection transitions to 'available'",
                "rollback_commands": [],
                "notes": ["Cross-connect provisioning typically 2–5 business days", "Verify fiber type: SMF for 10G/100G"],
                "time_est": "varies", "dod_ref": ""
            },
            {
                "number": 3, "title": "Create Private Virtual Interface",
                "platform": "aws-cli",
                "prerequisites": ["Connection in 'available' state", "VGW or DXGW ARN"],
                "actions": [{"type": "cli", "label": "Create private VIF", "command": "aws directconnect create-private-virtual-interface --connection-id <conn-id> --new-private-virtual-interface virtualInterfaceName=vif-il4-prod,vlan=100,asn=65001,authKey=<bgp-md5>,amazonAddress=169.254.0.1/30,customerAddress=169.254.0.2/30,virtualGatewayId=<vgw-id>", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws directconnect describe-virtual-interfaces --virtual-interface-id <vif-id> --query 'virtualInterfaces[0].virtualInterfaceState'", "expected_output": "\"available\""}],
                "expected_result": "BGP session UP; prefixes advertised",
                "rollback_commands": [{"command": "aws directconnect delete-virtual-interface --virtual-interface-id <vif-id>", "label": "Delete VIF"}],
                "notes": ["Use BGP MD5 auth — required for DoD", "VLAN must match colo patch panel config"],
                "time_est": "20m", "dod_ref": "NIST 800-53 SC-8"
            }
        ],
        "validation": ["BGP session shows Established", "Route table shows on-prem prefixes via DX", "Ping across VIF succeeds"],
        "rollback": {"steps": ["Delete VIF", "Cancel cross-connect with colo", "Delete DX connection"], "rto": "4h"},
        "escalation": [{"tier": 1, "contact": "AWS Enterprise Support", "condition": "Connection stuck in 'ordering' > 5 days"}],
        "classification": "CUI",
    },
    {
        "title": "AWS Direct Connect — Hosted Connection via Partner",
        "category": "physical_connectivity",
        "csp": "aws",
        "description": "Accept a hosted DX connection provisioned by an APN partner or DISA circuit provider. Covers acceptance workflow, hosted VIF creation, and BGP establishment.",
        "prerequisites": ["APN partner has provisioned hosted connection", "Connection invitation received in DX console"],
        "steps": [
            {
                "number": 1, "title": "Accept Hosted Connection",
                "platform": "aws-cli",
                "prerequisites": ["IAM permission: directconnect:ConfirmConnection"],
                "actions": [{"type": "cli", "label": "Accept connection", "command": "aws directconnect confirm-connection --connection-id <hosted-conn-id>", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws directconnect describe-connections --connection-id <hosted-conn-id> --query 'connections[0].connectionState'", "expected_output": "\"available\""}],
                "expected_result": "Hosted connection accepted and available",
                "rollback_commands": [],
                "notes": ["Hosted connections cannot be deleted by customer — coordinate with partner", "DISA NIPRNet on-ramp uses hosted connection model"],
                "time_est": "5m", "dod_ref": "SCCA FRD §2.1.1"
            },
            {
                "number": 2, "title": "Accept Hosted VIF",
                "platform": "aws-cli",
                "prerequisites": ["VIF invitation from partner"],
                "actions": [{"type": "cli", "label": "Confirm hosted VIF", "command": "aws directconnect confirm-private-virtual-interface --virtual-interface-id <hosted-vif-id> --virtual-gateway-id <vgw-id>", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws directconnect describe-virtual-interfaces --virtual-interface-id <hosted-vif-id> --query 'virtualInterfaces[0].virtualInterfaceState'", "expected_output": "\"available\""}],
                "expected_result": "VIF available; BGP session establishing",
                "rollback_commands": [],
                "notes": ["BGP MD5 key must match partner's config", "Transit VIF requires DXGW — cannot use VGW directly"],
                "time_est": "10m", "dod_ref": ""
            }
        ],
        "validation": ["VIF state: available", "BGP peering: Established", "VPC route table updated"],
        "rollback": {"steps": ["Contact partner to release hosted VIF", "Disassociate VGW"], "rto": "2h"},
        "escalation": [{"tier": 1, "contact": "Partner NOC / DISA NCC", "condition": "VIF stuck in 'confirming'"}],
        "classification": "CUI",
    },
    {
        "title": "Azure ExpressRoute — Circuit Provisioning and Private Peering",
        "category": "physical_connectivity",
        "csp": "azure",
        "description": "Provision an Azure ExpressRoute circuit via a connectivity provider, configure private peering, and link to a Virtual Network Gateway for DoD IL4/IL5 workloads.",
        "prerequisites": ["Azure Government subscription", "Connectivity provider chosen (AT&T, Equinix, etc.)", "ASN assigned", "/30 or /126 peering subnets allocated"],
        "steps": [
            {
                "number": 1, "title": "Create ExpressRoute Circuit",
                "platform": "az-cli",
                "prerequisites": ["az CLI logged in to AzureUSGovernment", "Resource group exists"],
                "actions": [{"type": "cli", "label": "Create ER circuit", "command": "az network express-route create --resource-group rg-network-prod --name er-circuit-il4 --location usgovvirginia --sku-family MeteredData --sku-tier Standard --bandwidth 1000 --peering-location 'Equinix Washington DC' --provider 'Equinix' --allow-classic-operations false", "cli_type": "az-cli"}],
                "verification": [{"command": "az network express-route show -g rg-network-prod -n er-circuit-il4 --query 'circuitProvisioningState'", "expected_output": "\"Enabled\""}],
                "expected_result": "Circuit created; service key generated for provider",
                "rollback_commands": [{"command": "az network express-route delete -g rg-network-prod -n er-circuit-il4", "label": "Delete circuit"}],
                "notes": ["Provide service key to connectivity provider for physical provisioning", "AzureUSGovernment cloud — use --environment AzureUSGovernment for az login"],
                "time_est": "10m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Configure Private Peering",
                "platform": "az-cli",
                "prerequisites": ["Provider has provisioned circuit (CircuitProvisioningState: Provisioned)"],
                "actions": [{"type": "cli", "label": "Create private peering", "command": "az network express-route peering create --circuit-name er-circuit-il4 -g rg-network-prod --peering-type AzurePrivatePeering --peer-asn 65001 --primary-peer-subnet 192.168.100.0/30 --secondary-peer-subnet 192.168.100.4/30 --vlan-id 100 --shared-key <bgp-auth-key>", "cli_type": "az-cli"}],
                "verification": [{"command": "az network express-route peering show --circuit-name er-circuit-il4 -g rg-network-prod --name AzurePrivatePeering --query 'peeringType'", "expected_output": "\"AzurePrivatePeering\""}],
                "expected_result": "Private peering configured; BGP sessions coming up",
                "rollback_commands": [{"command": "az network express-route peering delete --circuit-name er-circuit-il4 -g rg-network-prod --name AzurePrivatePeering", "label": "Remove peering"}],
                "notes": ["Use shared-key for BGP MD5 auth — DoD requirement", "Primary + secondary paths for redundancy (active/active)"],
                "time_est": "15m", "dod_ref": "NIST 800-53 SC-8"
            },
            {
                "number": 3, "title": "Link Circuit to Virtual Network Gateway",
                "platform": "az-cli",
                "prerequisites": ["ExpressRoute Gateway (az-ergw) deployed in hub VNet"],
                "actions": [{"type": "cli", "label": "Create VNet connection", "command": "az network vpn-connection create -g rg-network-prod -n conn-er-hub-il4 --vnet-gateway1 ergw-hub-il4 --express-route-circuit2 er-circuit-il4 --routing-weight 10", "cli_type": "az-cli"}],
                "verification": [{"command": "az network vpn-connection show -g rg-network-prod -n conn-er-hub-il4 --query 'connectionStatus'", "expected_output": "\"Connected\""}],
                "expected_result": "ER gateway connected; on-prem routes learned in VNet",
                "rollback_commands": [{"command": "az network vpn-connection delete -g rg-network-prod -n conn-er-hub-il4", "label": "Remove connection"}],
                "notes": ["FastPath requires UltraPerformance or ErGw3AZ SKU"],
                "time_est": "10m", "dod_ref": ""
            }
        ],
        "validation": ["Circuit state: Enabled + Provisioned", "Private peering BGP: Connected", "VNet route table shows on-prem prefixes"],
        "rollback": {"steps": ["Delete VNet connection", "Remove private peering config", "Contact provider to deprovision circuit"], "rto": "4h"},
        "escalation": [{"tier": 1, "contact": "Azure Gov Support", "condition": "BGP not establishing after 30min"}],
        "classification": "CUI",
    },
    {
        "title": "GCP Dedicated Interconnect — Connection and VLAN Attachment",
        "category": "physical_connectivity",
        "csp": "gcp",
        "description": "Provision a GCP Dedicated Interconnect at a supported colocation facility, create VLAN attachments, and establish BGP sessions to Cloud Router for DoD workloads.",
        "prerequisites": ["GCP project with Compute/Network APIs enabled", "Colocation at GCP interconnect location", "Cloud Router created in target VPC"],
        "steps": [
            {
                "number": 1, "title": "Create Dedicated Interconnect",
                "platform": "gcloud",
                "prerequisites": ["gcloud auth configured", "IAM: compute.interconnects.create"],
                "actions": [{"type": "cli", "label": "Create interconnect", "command": "gcloud compute interconnects create ic-prod-dc1 --interconnect-type DEDICATED --location iad-zone1-123 --requested-link-count 2 --link-type LINK_TYPE_ETHERNET_10G_LR --description 'DoD IL4 interconnect'", "cli_type": "gcloud"}],
                "verification": [{"command": "gcloud compute interconnects describe ic-prod-dc1 --format='value(operationalStatus)'", "expected_output": "OS_UNPROVISIONED"}],
                "expected_result": "Interconnect created; LOA issued for cross-connect request",
                "rollback_commands": [{"command": "gcloud compute interconnects delete ic-prod-dc1", "label": "Delete interconnect"}],
                "notes": ["Download LOA from console; submit to colo within 30 days", "Dedicated interconnect requires 10G or 100G ports"],
                "time_est": "15m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create VLAN Attachment",
                "platform": "gcloud",
                "prerequisites": ["Cloud Router exists in target region", "Interconnect in OS_ACTIVE state"],
                "actions": [{"type": "cli", "label": "Create VLAN attachment", "command": "gcloud compute interconnects attachments dedicated create vlan-att-il4 --interconnect ic-prod-dc1 --router cloud-router-il4 --region us-east4 --vlan 100 --bandwidth BPS_10G --description 'IL4 VLAN attachment'", "cli_type": "gcloud"}],
                "verification": [{"command": "gcloud compute interconnects attachments describe vlan-att-il4 --region us-east4 --format='value(state)'", "expected_output": "ACTIVE"}],
                "expected_result": "VLAN attachment active; BGP session auto-created on Cloud Router",
                "rollback_commands": [{"command": "gcloud compute interconnects attachments delete vlan-att-il4 --region us-east4", "label": "Delete attachment"}],
                "notes": ["BGP keys auto-generated; retrieve with: gcloud compute routers get-status", "For redundancy: create second attachment on different edge zone"],
                "time_est": "10m", "dod_ref": "NIST 800-53 SC-7"
            }
        ],
        "validation": ["Interconnect operational status: OS_ACTIVE", "VLAN attachment state: ACTIVE", "Cloud Router BGP session established"],
        "rollback": {"steps": ["Delete VLAN attachment", "Delete Cloud Router BGP peer", "Cancel interconnect"], "rto": "2h"},
        "escalation": [{"tier": 1, "contact": "GCP Enterprise Support", "condition": "OS_UNPROVISIONED after 5 business days"}],
        "classification": "CUI",
    },
    {
        "title": "OCI FastConnect — Virtual Circuit via Provider",
        "category": "physical_connectivity",
        "csp": "oci",
        "description": "Establish an OCI FastConnect virtual circuit via a supported provider (Equinix, AT&T, etc.), attach to DRG, and configure BGP for DoD FedRAMP/IL4 workloads.",
        "prerequisites": ["OCI tenancy in US Government region", "FastConnect provider relationship established", "DRG created and attached to VCN"],
        "steps": [
            {
                "number": 1, "title": "Create Virtual Circuit",
                "platform": "oci-cli",
                "prerequisites": ["OCI CLI configured", "Provider circuit confirmed"],
                "actions": [{"type": "cli", "label": "Create VC", "command": "oci network virtual-circuit create --compartment-id <compartment-ocid> --display-name fc-vc-il4-prod --type PRIVATE --bandwidth-shape-name 1 Gbps --gateway-id <drg-ocid> --provider-name 'Equinix' --provider-service-id <provider-service-ocid> --bgp-admin-state ENABLED --customer-bgp-asn 65001 --cross-connect-mappings '[{\"customerBgpPeeringIp\":\"192.168.200.2/30\",\"oracleBgpPeeringIp\":\"192.168.200.1/30\"}]'", "cli_type": "oci-cli"}],
                "verification": [{"command": "oci network virtual-circuit get --virtual-circuit-id <vc-ocid> --query 'data.\"lifecycle-state\"'", "expected_output": "\"PROVISIONED\""}],
                "expected_result": "Virtual circuit provisioned; BGP session UP",
                "rollback_commands": [{"command": "oci network virtual-circuit delete --virtual-circuit-id <vc-ocid>", "label": "Delete virtual circuit"}],
                "notes": ["OCI uses DRG as the gateway — VCN attachment must exist", "BGP MD5 auth: add --bgp-session-state-details if needed"],
                "time_est": "20m", "dod_ref": ""
            }
        ],
        "validation": ["VC lifecycle state: PROVISIONED", "BGP session: UP", "DRG route table shows on-prem prefixes"],
        "rollback": {"steps": ["Delete virtual circuit", "Contact provider to release port"], "rto": "2h"},
        "escalation": [{"tier": 1, "contact": "OCI Gov Support", "condition": "VC stuck in PROVISIONING > 2 days"}],
        "classification": "CUI",
    },
    {
        "title": "IBM Direct Link 2.0 Dedicated — Provisioning and BGP",
        "category": "physical_connectivity",
        "csp": "ibm",
        "description": "Order IBM Direct Link 2.0 Dedicated for private fiber connectivity to IBM Cloud, configure BGP with MD5 authentication, and attach to Transit Gateway for multi-VPC routing.",
        "prerequisites": ["IBM Cloud account (US Federal region)", "Physical fiber ordered at IBM PoP", "Transit Gateway or VPC Direct Link gateway created"],
        "steps": [
            {
                "number": 1, "title": "Create Direct Link Gateway",
                "platform": "ibmcloud-cli",
                "prerequisites": ["ibmcloud CLI logged in", "BGP ASN assigned"],
                "actions": [{"type": "cli", "label": "Create DL gateway", "command": "ibmcloud dl gateway-create --name dl-dedicated-prod --type dedicated --routing-instance <tgw-id> --bgp-asn 65001 --bgp-cer-cidr 192.168.10.2/29 --bgp-ibm-cidr 192.168.10.1/29 --speed-mbps 1000 --port <port-id> --cross-connect-router ibm-cr-dal10-1", "cli_type": "ibmcloud-cli"}],
                "verification": [{"command": "ibmcloud dl gateway <gateway-id> --output json | python3 -c \"import sys,json; print(json.load(sys.stdin)['operational_status'])\"", "expected_output": "provisioned"}],
                "expected_result": "Gateway provisioned; LOA issued for cross-connect",
                "rollback_commands": [{"command": "ibmcloud dl gateway-delete <gateway-id>", "label": "Delete gateway"}],
                "notes": ["BGP MD5 auth supported via --authentication-key param", "Dedicated DL requires physical cross-connect at IBM colocation"],
                "time_est": "15m", "dod_ref": ""
            }
        ],
        "validation": ["Gateway status: provisioned", "BGP session: active", "Transit Gateway route table updated"],
        "rollback": {"steps": ["Delete gateway", "Cancel cross-connect with IBM"], "rto": "4h"},
        "escalation": [{"tier": 1, "contact": "IBM Cloud Support", "condition": "Gateway stuck in 'create_pending'"}],
        "classification": "CUI",
    },
    {
        "title": "IBM Direct Link 2.0 Connect — NSP Provider Provisioning",
        "category": "physical_connectivity",
        "csp": "ibm",
        "description": "Establish IBM Direct Link 2.0 Connect via a Network Service Provider (NSP) for Layer 2/3 handoff without dedicated fiber. Covers gateway creation, provider acceptance, and BGP.",
        "prerequisites": ["NSP provider chosen (AT&T, Verizon, etc.)", "Provider circuit ordered", "IBM Cloud Transit Gateway available"],
        "steps": [
            {
                "number": 1, "title": "Create Connect Gateway",
                "platform": "ibmcloud-cli",
                "prerequisites": ["ibmcloud CLI configured", "Provider service ID obtained from IBM marketplace"],
                "actions": [{"type": "cli", "label": "Create DL Connect gateway", "command": "ibmcloud dl gateway-create --name dl-connect-prod --type connect --routing-instance <tgw-id> --bgp-asn 65002 --bgp-cer-cidr 192.168.20.2/29 --bgp-ibm-cidr 192.168.20.1/29 --speed-mbps 500 --provider-api-managed-port <provider-port-id>", "cli_type": "ibmcloud-cli"}],
                "verification": [{"command": "ibmcloud dl gateway <gateway-id> --output json | python3 -c \"import sys,json; print(json.load(sys.stdin)['operational_status'])\"", "expected_output": "provisioned"}],
                "expected_result": "Connect gateway provisioned after NSP approval",
                "rollback_commands": [{"command": "ibmcloud dl gateway-delete <gateway-id>", "label": "Delete gateway"}],
                "notes": ["Provider must approve connection request — may take 1-2 business days", "Connect DL: NSP manages physical layer; IBM manages BGP"],
                "time_est": "15m", "dod_ref": ""
            }
        ],
        "validation": ["Gateway operational status: provisioned", "BGP: active", "Prefixes learned in VPC/TGW"],
        "rollback": {"steps": ["Delete Connect gateway", "Contact NSP to release circuit"], "rto": "2h"},
        "escalation": [{"tier": 1, "contact": "NSP NOC + IBM Support", "condition": "Provider approval pending > 3 days"}],
        "classification": "CUI",
    },
]

# ── Category 2: VPN Configuration ─────────────────────────────────────────────

_VPN = [
    {
        "title": "AWS Site-to-Site VPN — IKEv2 BGP with Virtual Private Gateway",
        "category": "vpn_configuration",
        "csp": "aws",
        "description": "Configure AWS Site-to-Site VPN using IKEv2 with BGP dynamic routing via a Virtual Private Gateway (VGW). Covers CGW creation, VGW attachment, tunnel options with DoD-compliant cipher suite, and BGP peering.",
        "prerequisites": ["VPC exists", "On-prem firewall supports IKEv2 + BGP", "On-prem public IP known", "ASN assigned"],
        "steps": [
            {
                "number": 1, "title": "Create Customer Gateway",
                "platform": "aws-cli",
                "prerequisites": ["On-prem device public IP confirmed"],
                "actions": [{"type": "cli", "label": "Create CGW", "command": "aws ec2 create-customer-gateway --type ipsec.1 --public-ip <on-prem-public-ip> --bgp-asn 65100 --device-name 'palo-alto-fw-dc1' --region us-gov-east-1", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-customer-gateways --filters Name=tag:Name,Values=palo-alto-fw-dc1 --query 'CustomerGateways[0].State'", "expected_output": "\"available\""}],
                "expected_result": "CGW created with on-prem IP and ASN registered",
                "rollback_commands": [{"command": "aws ec2 delete-customer-gateway --customer-gateway-id <cgw-id>", "label": "Delete CGW"}],
                "notes": ["CGW public IP must be static and reachable from AWS", "Private ASN range: 64512-65534"],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create and Attach Virtual Private Gateway",
                "platform": "aws-cli",
                "prerequisites": ["VPC ID available"],
                "actions": [{"type": "cli", "label": "Create VGW", "command": "aws ec2 create-vpn-gateway --type ipsec.1 --amazon-side-asn 64512 --region us-gov-east-1 && aws ec2 attach-vpn-gateway --vpn-gateway-id <vgw-id> --vpc-id <vpc-id>", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-vpn-gateways --filters Name=attachment.vpc-id,Values=<vpc-id> --query 'VpnGateways[0].VpcAttachments[0].State'", "expected_output": "\"attached\""}],
                "expected_result": "VGW attached to VPC",
                "rollback_commands": [{"command": "aws ec2 detach-vpn-gateway --vpn-gateway-id <vgw-id> --vpc-id <vpc-id> && aws ec2 delete-vpn-gateway --vpn-gateway-id <vgw-id>", "label": "Detach and delete VGW"}],
                "notes": ["Enable route propagation on VPC route tables after attachment"],
                "time_est": "10m", "dod_ref": ""
            },
            {
                "number": 3, "title": "Create VPN Connection with DoD Cipher Suite",
                "platform": "aws-cli",
                "prerequisites": ["VGW attached", "CGW created"],
                "actions": [{"type": "cli", "label": "Create VPN connection", "command": "aws ec2 create-vpn-connection --type ipsec.1 --customer-gateway-id <cgw-id> --vpn-gateway-id <vgw-id> --options '{\"StaticRoutesOnly\":false,\"TunnelOptions\":[{\"Phase1EncryptionAlgorithms\":[{\"Value\":\"AES256-GCM-16\"}],\"Phase2EncryptionAlgorithms\":[{\"Value\":\"AES256-GCM-16\"}],\"Phase1IntegrityAlgorithms\":[{\"Value\":\"SHA2-384\"}],\"Phase2IntegrityAlgorithms\":[{\"Value\":\"SHA2-384\"}],\"Phase1DHGroupNumbers\":[{\"Value\":20}],\"Phase2DHGroupNumbers\":[{\"Value\":20}],\"IKEVersions\":[{\"Value\":\"ikev2\"}],\"DPDTimeoutAction\":\"restart\",\"DPDTimeoutSeconds\":30}]}' --region us-gov-east-1", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-vpn-connections --vpn-connection-ids <vpn-id> --query 'VpnConnections[0].VgwTelemetry[*].Status'", "expected_output": "[\"UP\",\"UP\"]"}],
                "expected_result": "Both tunnels UP; BGP sessions established",
                "rollback_commands": [{"command": "aws ec2 delete-vpn-connection --vpn-connection-id <vpn-id>", "label": "Delete VPN connection"}],
                "notes": ["DOD REQUIREMENT: IKEv2 only (CNSSP-15)", "AES-256-GCM + SHA-384 + DH Group 20 (ECP-384) required for IL4/IL5", "Enable NAT-T if behind NAT device"],
                "time_est": "15m", "dod_ref": "CNSSP-15 §4.2"
            },
            {
                "number": 4, "title": "Enable Route Propagation",
                "platform": "aws-cli",
                "prerequisites": ["VPN tunnels UP"],
                "actions": [{"type": "cli", "label": "Enable route propagation", "command": "aws ec2 enable-vgw-route-propagation --gateway-id <vgw-id> --route-table-id <rtb-id>", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-route-tables --route-table-ids <rtb-id> --query 'RouteTables[0].PropagatingVgws'", "expected_output": "[{\"GatewayId\":\"<vgw-id>\"}]"}],
                "expected_result": "On-prem prefixes appear as propagated routes in VPC route table",
                "rollback_commands": [{"command": "aws ec2 disable-vgw-route-propagation --gateway-id <vgw-id> --route-table-id <rtb-id>", "label": "Disable propagation"}],
                "notes": [],
                "time_est": "2m", "dod_ref": ""
            }
        ],
        "validation": ["Both VPN tunnels: UP", "BGP session: Established", "On-prem prefixes in VPC route table", "Traffic flow test passes"],
        "rollback": {"steps": ["Delete VPN connection", "Detach VGW", "Delete VGW", "Delete CGW"], "rto": "1h"},
        "escalation": [{"tier": 1, "contact": "AWS Enterprise Support", "condition": "Tunnels DOWN after 30 min debug"}],
        "classification": "CUI",
    },
    {
        "title": "AWS Site-to-Site VPN HA — Active/Active Dual-Tunnel via TGW",
        "category": "vpn_configuration",
        "csp": "aws",
        "description": "Deploy highly-available active/active S2S VPN using Transit Gateway (TGW) with two VPN connections (4 tunnels total) for dual-router redundancy. ECMP load balancing enabled.",
        "prerequisites": ["TGW exists", "Two on-prem edge devices or dual WAN circuits", "ECMP-capable on-prem routing"],
        "steps": [
            {
                "number": 1, "title": "Create Two CGWs for Dual-Router Setup",
                "platform": "aws-cli",
                "prerequisites": ["Two on-prem public IPs confirmed"],
                "actions": [{"type": "cli", "label": "Create CGW pair", "command": "aws ec2 create-customer-gateway --type ipsec.1 --public-ip <router1-ip> --bgp-asn 65100 --region us-gov-east-1\naws ec2 create-customer-gateway --type ipsec.1 --public-ip <router2-ip> --bgp-asn 65100 --region us-gov-east-1", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-customer-gateways --query 'CustomerGateways[?State==`available`].CustomerGatewayId'", "expected_output": "[\"cgw-1\",\"cgw-2\"]"}],
                "expected_result": "Both CGWs in available state",
                "rollback_commands": [],
                "notes": ["Both CGWs use same ASN for ECMP BGP", "Each CGW represents a physical router"],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create VPN Connections on TGW",
                "platform": "aws-cli",
                "prerequisites": ["TGW ID available", "ECMP enabled on TGW"],
                "actions": [{"type": "cli", "label": "Create HA VPN connections", "command": "aws ec2 create-vpn-connection --type ipsec.1 --customer-gateway-id <cgw1-id> --transit-gateway-id <tgw-id> --options '{\"EnableAcceleration\":true,\"StaticRoutesOnly\":false}' --region us-gov-east-1\naws ec2 create-vpn-connection --type ipsec.1 --customer-gateway-id <cgw2-id> --transit-gateway-id <tgw-id> --options '{\"EnableAcceleration\":true,\"StaticRoutesOnly\":false}'", "cli_type": "aws-cli"}],
                "verification": [{"command": "aws ec2 describe-vpn-connections --query 'VpnConnections[?TransitGatewayId==`<tgw-id>`].VgwTelemetry[*].Status' --output text", "expected_output": "UP UP UP UP"}],
                "expected_result": "4 tunnels UP across 2 VPN connections",
                "rollback_commands": [{"command": "aws ec2 delete-vpn-connection --vpn-connection-id <vpn-id>", "label": "Delete each VPN connection"}],
                "notes": ["Accelerated VPN uses Global Accelerator for consistent latency", "ECMP: TGW must have ecmpSupport=enable"],
                "time_est": "20m", "dod_ref": "SCCA FRD §3.2.2"
            }
        ],
        "validation": ["4 of 4 tunnels UP", "BGP 4 sessions established", "ECMP routes visible in TGW route table", "Failover test: disconnect router1, traffic continues via router2"],
        "rollback": {"steps": ["Delete both VPN connections", "Delete TGW route table entries", "Delete CGWs"], "rto": "1h"},
        "escalation": [{"tier": 1, "contact": "AWS Enterprise Support", "condition": "< 4 tunnels UP after IKE debug"}],
        "classification": "CUI",
    },
    {
        "title": "Azure VPN Gateway — Active-Active S2S VPN with BGP",
        "category": "vpn_configuration",
        "csp": "azure",
        "description": "Deploy an Azure VPN Gateway in active-active mode with BGP for high-availability site-to-site connectivity. Covers gateway deployment, Local Network Gateway creation, and connection configuration.",
        "prerequisites": ["GatewaySubnet /27 or larger in hub VNet", "On-prem public IP and ASN", "Azure Government subscription"],
        "steps": [
            {
                "number": 1, "title": "Deploy VPN Gateway (Active-Active)",
                "platform": "az-cli",
                "prerequisites": ["GatewaySubnet exists in VNet"],
                "actions": [{"type": "cli", "label": "Create VPN gateway", "command": "az network vnet-gateway create -g rg-network-prod -n vpngw-hub-il4 --vnet vnet-hub-il4 --gateway-type Vpn --vpn-type RouteBased --sku VpnGw2AZ --active-active true --asn 65515 --bgp-peering-address 169.254.21.1 --public-ip-address vpngw-pip1 vpngw-pip2 --location usgovvirginia", "cli_type": "az-cli"}],
                "verification": [{"command": "az network vnet-gateway show -g rg-network-prod -n vpngw-hub-il4 --query 'provisioningState'", "expected_output": "\"Succeeded\""}],
                "expected_result": "Active-active VPN gateway deployed (~30-45 min)",
                "rollback_commands": [{"command": "az network vnet-gateway delete -g rg-network-prod -n vpngw-hub-il4", "label": "Delete VPN gateway"}],
                "notes": ["VpnGw2AZ provides zone-redundant deployment", "Active-active requires two public IPs"],
                "time_est": "45m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create Local Network Gateway",
                "platform": "az-cli",
                "prerequisites": ["On-prem IP and prefixes confirmed"],
                "actions": [{"type": "cli", "label": "Create LNG", "command": "az network local-gateway create -g rg-network-prod -n lng-onprem-dc1 --gateway-ip-address <on-prem-public-ip> --asn 65100 --bgp-peering-address 169.254.21.2 --address-prefixes 10.0.0.0/8", "cli_type": "az-cli"}],
                "verification": [{"command": "az network local-gateway show -g rg-network-prod -n lng-onprem-dc1 --query 'provisioningState'", "expected_output": "\"Succeeded\""}],
                "expected_result": "LNG created representing on-prem endpoint",
                "rollback_commands": [{"command": "az network local-gateway delete -g rg-network-prod -n lng-onprem-dc1", "label": "Delete LNG"}],
                "notes": [],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 3, "title": "Create VPN Connection with IKEv2 Policy",
                "platform": "az-cli",
                "prerequisites": ["VPN gateway and LNG deployed"],
                "actions": [{"type": "cli", "label": "Create connection with custom policy", "command": "az network vpn-connection create -g rg-network-prod -n conn-s2s-dc1 --vnet-gateway1 vpngw-hub-il4 --local-gateway2 lng-onprem-dc1 --shared-key '<psk>' --enable-bgp true && az network vpn-connection ipsec-policy add -g rg-network-prod --connection-name conn-s2s-dc1 --ike-encryption AES256 --ike-integrity SHA384 --dh-group DHGroup20 --ipsec-encryption GCMAES256 --ipsec-integrity GCMAES256 --pfs-group PFS20 --sa-lifetime 27000 --sa-datasize 102400000", "cli_type": "az-cli"}],
                "verification": [{"command": "az network vpn-connection show -g rg-network-prod -n conn-s2s-dc1 --query 'connectionStatus'", "expected_output": "\"Connected\""}],
                "expected_result": "VPN connection established with BGP",
                "rollback_commands": [{"command": "az network vpn-connection delete -g rg-network-prod -n conn-s2s-dc1", "label": "Delete connection"}],
                "notes": ["DOD REQUIREMENT: AES-256-GCM, SHA-384, DH Group 20 (CNSSP-15)", "BGP over VPN uses APIPA 169.254.x.x addresses"],
                "time_est": "10m", "dod_ref": "CNSSP-15 §4.2"
            }
        ],
        "validation": ["Connection status: Connected", "BGP learned routes in VNet effective routes", "Bidirectional traffic test"],
        "rollback": {"steps": ["Delete connection", "Delete LNG", "Delete VPN gateway"], "rto": "2h"},
        "escalation": [{"tier": 1, "contact": "Azure Gov Support", "condition": "Connection stuck in 'Connecting' > 30min"}],
        "classification": "CUI",
    },
    {
        "title": "GCP HA VPN Gateway — Dual-Redundant Tunnels with BGP",
        "category": "vpn_configuration",
        "csp": "gcp",
        "description": "Deploy GCP HA VPN gateway with two interfaces, create external VPN gateway representation, configure four tunnels for 99.99% SLA, and establish BGP via Cloud Router.",
        "prerequisites": ["Cloud Router in target region", "On-prem peer device supports IKEv2 + BGP", "Two on-prem public IPs for HA"],
        "steps": [
            {
                "number": 1, "title": "Create HA VPN Gateway",
                "platform": "gcloud",
                "prerequisites": ["gcloud configured", "VPC network exists"],
                "actions": [{"type": "cli", "label": "Create HA VPN GW", "command": "gcloud compute vpn-gateways create ha-vpn-gw-prod --network vpc-il4-prod --region us-east4 --stack-type IPV4_ONLY", "cli_type": "gcloud"}],
                "verification": [{"command": "gcloud compute vpn-gateways describe ha-vpn-gw-prod --region us-east4 --format='value(vpnInterfaces[0].ipAddress,vpnInterfaces[1].ipAddress)'", "expected_output": "<ip1>\n<ip2>"}],
                "expected_result": "HA VPN gateway created with 2 interface IPs",
                "rollback_commands": [{"command": "gcloud compute vpn-gateways delete ha-vpn-gw-prod --region us-east4", "label": "Delete HA VPN gateway"}],
                "notes": ["HA VPN provides 99.99% SLA with 2 tunnels minimum"],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create External VPN Gateway and Tunnels",
                "platform": "gcloud",
                "prerequisites": ["HA VPN GW created", "On-prem IPs confirmed"],
                "actions": [{"type": "cli", "label": "Create external peer GW", "command": "gcloud compute external-vpn-gateways create on-prem-gw --interfaces 0=<onprem-ip1>,1=<onprem-ip2>\ngcloud compute vpn-tunnels create tunnel-1 --peer-external-gateway on-prem-gw --peer-external-gateway-interface 0 --region us-east4 --ike-version 2 --shared-secret <psk> --router cloud-router-il4 --vpn-gateway ha-vpn-gw-prod --vpn-gateway-interface 0\ngcloud compute vpn-tunnels create tunnel-2 --peer-external-gateway on-prem-gw --peer-external-gateway-interface 1 --region us-east4 --ike-version 2 --shared-secret <psk> --router cloud-router-il4 --vpn-gateway ha-vpn-gw-prod --vpn-gateway-interface 1", "cli_type": "gcloud"}],
                "verification": [{"command": "gcloud compute vpn-tunnels list --filter='name:tunnel-*' --format='value(name,status)'", "expected_output": "tunnel-1 ESTABLISHED\ntunnel-2 ESTABLISHED"}],
                "expected_result": "Both tunnels established",
                "rollback_commands": [{"command": "gcloud compute vpn-tunnels delete tunnel-1 tunnel-2 --region us-east4", "label": "Delete tunnels"}],
                "notes": ["IKEv2 only — GCP does not support IKEv1 on HA VPN", "PSK minimum 30 chars for DoD"],
                "time_est": "15m", "dod_ref": "CNSSP-15 §4.2"
            },
            {
                "number": 3, "title": "Configure BGP Sessions on Cloud Router",
                "platform": "gcloud",
                "prerequisites": ["Tunnels ESTABLISHED"],
                "actions": [{"type": "cli", "label": "Add BGP peers", "command": "gcloud compute routers add-bgp-peer cloud-router-il4 --peer-name bgp-tunnel1 --peer-ip 169.254.0.2 --peer-asn 65100 --interface tunnel-1 --region us-east4\ngcloud compute routers add-bgp-peer cloud-router-il4 --peer-name bgp-tunnel2 --peer-ip 169.254.0.6 --peer-asn 65100 --interface tunnel-2 --region us-east4", "cli_type": "gcloud"}],
                "verification": [{"command": "gcloud compute routers get-status cloud-router-il4 --region us-east4 --format='json' | python3 -c \"import sys,json; [print(p['name'],p['status']) for p in json.load(sys.stdin)['result']['bgpPeerStatus']]\"", "expected_output": "bgp-tunnel1 {UP}\nbgp-tunnel2 {UP}"}],
                "expected_result": "BGP sessions UP on both tunnels",
                "rollback_commands": [],
                "notes": [],
                "time_est": "10m", "dod_ref": ""
            }
        ],
        "validation": ["Tunnel status: ESTABLISHED", "BGP state: UP on both peers", "Routes exchanged", "Failover: disable tunnel-1, traffic routes via tunnel-2"],
        "rollback": {"steps": ["Remove BGP peers", "Delete tunnels", "Delete external GW", "Delete HA VPN GW"], "rto": "1h"},
        "escalation": [{"tier": 1, "contact": "GCP Enterprise Support", "condition": "Tunnels stuck in WAITING_FOR_FULL_CONFIG"}],
        "classification": "CUI",
    },
    {
        "title": "OCI VPN Connect — IPSec Tunnel to DRG",
        "category": "vpn_configuration",
        "csp": "oci",
        "description": "Create OCI VPN Connect (IPSec connection) from on-prem to OCI Dynamic Routing Gateway using IKEv2 with BGP dynamic routing.",
        "prerequisites": ["DRG created and attached to VCN", "On-prem device supports IKEv2 + BGP", "CPE object created in OCI"],
        "steps": [
            {
                "number": 1, "title": "Create CPE Object",
                "platform": "oci-cli",
                "prerequisites": ["On-prem public IP confirmed"],
                "actions": [{"type": "cli", "label": "Create CPE", "command": "oci network cpe create --compartment-id <compartment-ocid> --display-name cpe-onprem-dc1 --ip-address <on-prem-public-ip> --cpe-device-shape-id <cisco-asa-shape-id>", "cli_type": "oci-cli"}],
                "verification": [{"command": "oci network cpe get --cpe-id <cpe-ocid> --query 'data.\"lifecycle-state\"'", "expected_output": "\"AVAILABLE\""}],
                "expected_result": "CPE object representing on-prem device created",
                "rollback_commands": [{"command": "oci network cpe delete --cpe-id <cpe-ocid>", "label": "Delete CPE"}],
                "notes": ["CPE device shape provides device-specific config template"],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 2, "title": "Create IPSec Connection",
                "platform": "oci-cli",
                "prerequisites": ["CPE and DRG exist"],
                "actions": [{"type": "cli", "label": "Create IPSec connection", "command": "oci network ip-sec-connection create --compartment-id <compartment-ocid> --cpe-id <cpe-ocid> --drg-id <drg-ocid> --static-routes '[]' --tunnel-configuration '[{\"routing\":\"BGP\",\"ikeVersion\":\"V2\",\"sharedSecret\":\"<psk>\",\"bgpSessionConfig\":{\"customerInterfaceIp\":\"10.0.0.1/30\",\"oracleInterfaceIp\":\"10.0.0.2/30\",\"customerBgpAsn\":\"65100\"}},{\"routing\":\"BGP\",\"ikeVersion\":\"V2\",\"sharedSecret\":\"<psk>\",\"bgpSessionConfig\":{\"customerInterfaceIp\":\"10.0.0.5/30\",\"oracleInterfaceIp\":\"10.0.0.6/30\",\"customerBgpAsn\":\"65100\"}}]'", "cli_type": "oci-cli"}],
                "verification": [{"command": "oci network ip-sec-connection-tunnel list --ipsc-id <ipsc-ocid> --query 'data[*].\"lifecycle-state\"'", "expected_output": "[\"UP\",\"UP\"]"}],
                "expected_result": "Both tunnels UP with BGP established",
                "rollback_commands": [{"command": "oci network ip-sec-connection delete --ipsc-id <ipsc-ocid>", "label": "Delete IPSec connection"}],
                "notes": ["OCI always provisions two tunnels (redundancy mandatory)", "IKEv2 with BGP is preferred over static routing"],
                "time_est": "15m", "dod_ref": ""
            }
        ],
        "validation": ["Tunnel state: UP", "BGP session UP on both tunnels", "DRG route table shows on-prem prefixes"],
        "rollback": {"steps": ["Delete IPSec connection", "Delete CPE"], "rto": "30m"},
        "escalation": [{"tier": 1, "contact": "OCI Gov Support", "condition": "Tunnel status DOWN after IKE debug"}],
        "classification": "CUI",
    },
    {
        "title": "IBM VPN for VPC — Site-to-Site IKEv2",
        "category": "vpn_configuration",
        "csp": "ibm",
        "description": "Configure IBM Cloud VPN for VPC (site-to-site) using IKEv2 with custom IKE and IPsec policies for DoD-compliant encryption.",
        "prerequisites": ["VPC exists in IBM Cloud US Federal region", "On-prem device supports IKEv2"],
        "steps": [
            {
                "number": 1, "title": "Create IKE and IPsec Policies",
                "platform": "ibmcloud-cli",
                "prerequisites": ["ibmcloud is plugin installed"],
                "actions": [{"type": "cli", "label": "Create DoD IKE policy", "command": "ibmcloud is ike-policy-create ike-dod-il4 --authentication-algorithm sha384 --encryption-algorithm aes256 --dh-group 20 --ike-version 2 --key-lifetime 28800\nibmcloud is ipsec-policy-create ipsec-dod-il4 --authentication-algorithm sha384 --encryption-algorithm aes256gcm16 --pfs dh20 --key-lifetime 3600", "cli_type": "ibmcloud-cli"}],
                "verification": [{"command": "ibmcloud is ike-policies --output json | python3 -c \"import sys,json; [print(p['name'],p['status']) for p in json.load(sys.stdin)]\"", "expected_output": "ike-dod-il4 stable"}],
                "expected_result": "IKE and IPsec policies created with DoD cipher suite",
                "rollback_commands": [],
                "notes": ["DOD REQUIREMENT: AES-256-GCM16, SHA-384, DH Group 20"],
                "time_est": "5m", "dod_ref": "CNSSP-15"
            },
            {
                "number": 2, "title": "Create VPN Gateway and Connection",
                "platform": "ibmcloud-cli",
                "prerequisites": ["Subnet for VPN GW identified", "IKE/IPsec policies created"],
                "actions": [{"type": "cli", "label": "Create VPN gateway", "command": "ibmcloud is vpn-gateway-create vpngw-prod --subnet <subnet-id> --mode route\nibmcloud is vpn-gateway-connection-create conn-dc1 <vpngw-id> <on-prem-public-ip> <psk> --ike-policy <ike-policy-id> --ipsec-policy <ipsec-policy-id> --peer-cidrs 10.0.0.0/8 --local-cidrs 172.16.0.0/12", "cli_type": "ibmcloud-cli"}],
                "verification": [{"command": "ibmcloud is vpn-gateway-connection <vpngw-id> <conn-id> --output json | python3 -c \"import sys,json; print(json.load(sys.stdin)['status'])\"", "expected_output": "up"}],
                "expected_result": "VPN connection UP",
                "rollback_commands": [{"command": "ibmcloud is vpn-gateway-connection-delete <vpngw-id> <conn-id>", "label": "Delete connection"}],
                "notes": ["Route mode VPN for dynamic routing", "Policy mode for static routes"],
                "time_est": "15m", "dod_ref": ""
            }
        ],
        "validation": ["VPN connection status: up", "Routes propagated to VPC", "Bidirectional traffic test"],
        "rollback": {"steps": ["Delete VPN connection", "Delete VPN gateway"], "rto": "30m"},
        "escalation": [{"tier": 1, "contact": "IBM Cloud Support", "condition": "Connection status 'down' after PSK verification"}],
        "classification": "CUI",
    },
    {
        "title": "On-Premises CE — Cisco IOS-XE IKEv2 VPN Peer Configuration",
        "category": "vpn_configuration",
        "csp": "multi",
        "description": "Configure Cisco IOS-XE customer edge router as IKEv2/IPSec VPN peer to AWS/Azure/GCP. DoD-compliant cipher suite, BGP peering over VTI, and DPD settings.",
        "prerequisites": ["Cisco IOS-XE 16.9+ or IOS-XR", "Static public IP or DISA MPLS handoff", "BGP ASN assigned"],
        "steps": [
            {
                "number": 1, "title": "Configure IKEv2 Proposal and Policy",
                "platform": "cisco-ios",
                "prerequisites": ["Console or SSH access to CE router"],
                "actions": [{"type": "cli", "label": "IKEv2 proposal", "command": "crypto ikev2 proposal DOD-PROP\n encryption aes-cbc-256\n integrity sha384\n group 20\n!\ncrypto ikev2 policy DOD-POLICY\n proposal DOD-PROP\n!", "cli_type": "cisco-ios"}],
                "verification": [{"command": "show crypto ikev2 proposal", "expected_output": "DOD-PROP ... AES-CBC-256 ... SHA384 ... DH 20"}],
                "expected_result": "IKEv2 proposal and policy configured",
                "rollback_commands": [{"command": "no crypto ikev2 proposal DOD-PROP\nno crypto ikev2 policy DOD-POLICY", "label": "Remove IKEv2 config"}],
                "notes": ["CNSSP-15 requires AES-256, SHA-384 minimum", "Group 20 = ECP-384 — approved for SECRET and below"],
                "time_est": "10m", "dod_ref": "CNSSP-15 §4.2"
            },
            {
                "number": 2, "title": "Configure IPSec Transform and Profile",
                "platform": "cisco-ios",
                "prerequisites": [],
                "actions": [{"type": "cli", "label": "IPSec transform set", "command": "crypto ipsec transform-set DOD-TS esp-aes 256 esp-sha384-hmac\n mode tunnel\n!\ncrypto ipsec profile DOD-PROF\n set transform-set DOD-TS\n set ikev2-profile DOD-IKEV2-PROF\n set pfs group20\n set security-association lifetime seconds 3600", "cli_type": "cisco-ios"}],
                "verification": [{"command": "show crypto ipsec transform-set", "expected_output": "DOD-TS ... esp-256-aes ... esp-sha384-hmac"}],
                "expected_result": "Transform set configured",
                "rollback_commands": [],
                "notes": ["PFS Group 20 mandatory for DoD", "SA lifetime 3600s = 1h for IL4"],
                "time_est": "5m", "dod_ref": ""
            },
            {
                "number": 3, "title": "Configure VTI and BGP",
                "platform": "cisco-ios",
                "prerequisites": ["Cloud VPN gateway IP known"],
                "actions": [{"type": "cli", "label": "VTI tunnel interface", "command": "interface Tunnel0\n ip address 169.254.0.2 255.255.255.252\n tunnel source GigabitEthernet0/0\n tunnel destination <cloud-vpn-ip>\n tunnel mode ipsec ipv4\n tunnel protection ipsec profile DOD-PROF\n!\nrouter bgp 65100\n neighbor 169.254.0.1 remote-as 64512\n neighbor 169.254.0.1 activate\n address-family ipv4\n  network 10.0.0.0 mask 255.0.0.0\n  neighbor 169.254.0.1 activate", "cli_type": "cisco-ios"}],
                "verification": [{"command": "show bgp neighbors 169.254.0.1 | include BGP state", "expected_output": "BGP state = Established"}],
                "expected_result": "VTI up, BGP established to cloud",
                "rollback_commands": [{"command": "no interface Tunnel0", "label": "Remove tunnel"}],
                "notes": ["DPD: keepalive 10 retry 3 — prevents stale SA", "Adjust MTU: ip mtu 1436 on tunnel interface"],
                "time_est": "15m", "dod_ref": ""
            }
        ],
        "validation": ["Tunnel interface UP/UP", "BGP session Established", "Cloud prefixes in routing table", "Ping through tunnel"],
        "rollback": {"steps": ["Remove tunnel interface", "Remove BGP neighbor", "Remove IPSec/IKEv2 config"], "rto": "30m"},
        "escalation": [{"tier": 1, "contact": "NOC / DISA circuit team", "condition": "Phase 1 not completing — check firewall ACLs UDP 500/4500"}],
        "classification": "CUI",
    },
    {
        "title": "On-Premises CE — Juniper SRX/JunOS IKEv2 VPN Peer Configuration",
        "category": "vpn_configuration",
        "csp": "multi",
        "description": "Configure Juniper SRX or MX series router as IKEv2/IPSec VPN peer to cloud CSPs. DoD cipher suite, st0 secure tunnel interface, BGP peering.",
        "prerequisites": ["JunOS 19.1+ on SRX or MX", "External interface with public IP", "BGP ASN"],
        "steps": [
            {
                "number": 1, "title": "Configure IKEv2 Proposal, Policy, and Gateway",
                "platform": "junos",
                "prerequisites": ["CLI access in configuration mode"],
                "actions": [{"type": "cli", "label": "IKEv2 config", "command": "set security ike proposal DOD-IKE-PROP authentication-method pre-shared-keys\nset security ike proposal DOD-IKE-PROP dh-group group20\nset security ike proposal DOD-IKE-PROP authentication-algorithm sha-384\nset security ike proposal DOD-IKE-PROP encryption-algorithm aes-256-cbc\nset security ike proposal DOD-IKE-PROP lifetime-seconds 28800\nset security ike policy DOD-IKE-POL mode main\nset security ike policy DOD-IKE-POL proposals DOD-IKE-PROP\nset security ike policy DOD-IKE-POL pre-shared-key ascii-text <psk>\nset security ike gateway DOD-GW ike-policy DOD-IKE-POL\nset security ike gateway DOD-GW address <cloud-gw-ip>\nset security ike gateway DOD-GW external-interface ge-0/0/0\nset security ike gateway DOD-GW version v2-only", "cli_type": "junos"}],
                "verification": [{"command": "show security ike security-associations", "expected_output": "State: UP"}],
                "expected_result": "IKE Phase 1 UP",
                "rollback_commands": [{"command": "delete security ike", "label": "Remove all IKE config"}],
                "notes": ["v2-only enforces IKEv2 — no IKEv1 fallback (DoD requirement)"],
                "time_est": "15m", "dod_ref": "CNSSP-15"
            },
            {
                "number": 2, "title": "Configure IPSec and Secure Tunnel Interface",
                "platform": "junos",
                "prerequisites": ["IKE Phase 1 UP"],
                "actions": [{"type": "cli", "label": "IPSec and st0", "command": "set security ipsec proposal DOD-IPSEC-PROP protocol esp\nset security ipsec proposal DOD-IPSEC-PROP authentication-algorithm hmac-sha384-192\nset security ipsec proposal DOD-IPSEC-PROP encryption-algorithm aes-256-gcm\nset security ipsec proposal DOD-IPSEC-PROP lifetime-seconds 3600\nset security ipsec policy DOD-IPSEC-POL perfect-forward-secrecy keys group20\nset security ipsec policy DOD-IPSEC-POL proposals DOD-IPSEC-PROP\nset security ipsec vpn CLOUD-VPN bind-interface st0.0\nset security ipsec vpn CLOUD-VPN ike gateway DOD-GW\nset security ipsec vpn CLOUD-VPN ike ipsec-policy DOD-IPSEC-POL\nset security ipsec vpn CLOUD-VPN establish-tunnels immediately\nset interfaces st0 unit 0 family inet address 169.254.0.2/30", "cli_type": "junos"}],
                "verification": [{"command": "show security ipsec security-associations", "expected_output": "State: up-active"}],
                "expected_result": "IPSec tunnel up on st0.0",
                "rollback_commands": [],
                "notes": ["aes-256-gcm provides authenticated encryption — no separate HMAC needed for payload", "PFS group20 mandatory"],
                "time_est": "10m", "dod_ref": ""
            }
        ],
        "validation": ["IKE SA: UP", "IPSec SA: up-active", "BGP neighbor established", "Traffic over st0.0"],
        "rollback": {"steps": ["Deactivate IPSec VPN config", "Remove st0 interface"], "rto": "30m"},
        "escalation": [{"tier": 1, "contact": "Network team / Juniper TAC", "condition": "Phase 2 fails — check PFS group mismatch"}],
        "classification": "CUI",
    },
]
