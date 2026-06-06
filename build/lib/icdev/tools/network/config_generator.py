# [CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Device Configuration Generator.

Generates vendor-specific configuration files (Cisco IOS, Arista EOS, Juniper JunOS)
from canvas topology data (nodes, edges, assigned IPs, VLANs, routing protocols).

Pure functions: take a graph dict, return a dict of {filename: config_text}.
No Flask dependency.

Supported OS types (derived from node type or node 'os' property):
  ios_router  — Cisco IOS (routers, firewalls)
  ios_switch  — Cisco IOS (L2/L3 switches)
  eos         — Arista EOS (switches, routers)
  junos       — Juniper JunOS (all device types)
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from typing import Any

try:
    from jinja2 import Environment, BaseLoader

    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Device types that receive a generated config file
_CONFIGURABLE_TYPES: frozenset[str] = frozenset(
    {
        "router",
        "switch-l2",
        "switch-l3",
        "firewall",
        "wlc",
        "sd-wan-edge",
    }
)

# Default OS per device type  (can be overridden by node property "os")
_DEFAULT_OS: dict[str, str] = {
    "router": "ios_router",
    "switch-l2": "ios_switch",
    "switch-l3": "ios_switch",
    "firewall": "ios_router",
    "wlc": "ios_switch",
    "sd-wan-edge": "ios_router",
    # ISP/carrier device types
    "pe-router": "ios_xr",
    "p-router": "ios_xr",
    "asr": "ios_xr",
    "ncs": "ios_xr",
    "sr-os": "nokia_sros",
    "7750sr": "nokia_sros",
    "7950xrs": "nokia_sros",
}


# Interface name generators per OS
def _ios_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return f"Loopback{index}"
    return f"GigabitEthernet0/{index}"


def _eos_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return f"Loopback{index}"
    return f"Ethernet{index + 1}"


def _junos_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return "lo0"
    return f"ge-0/0/{index}"


def _ios_xr_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return f"Loopback{index}"
    return f"HundredGigE0/0/0/{index}"


def _nokia_sros_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return "system"
    return f"1/1/{index + 1}"


def _panos_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return f"loopback.{index + 1}"
    return f"ethernet1/{index + 1}"


def _fortios_iface(index: int, is_loopback: bool = False) -> str:
    if is_loopback:
        return "lo0"
    return f"port{index + 1}"


_IFACE_GEN: dict[str, Any] = {
    "ios_router": _ios_iface,
    "ios_switch": _ios_iface,
    "eos": _eos_iface,
    "junos": _junos_iface,
    "ios_xr": _ios_xr_iface,
    "nokia_sros": _nokia_sros_iface,
    "panos": _panos_iface,
    "fortios": _fortios_iface,
}

# ---------------------------------------------------------------------------
# Jinja2 templates (embedded)
# ---------------------------------------------------------------------------

_IOS_ROUTER_TEMPLATE = """\
! ============================================================
! ICDEV(tm) Network Canvas -- Generated Configuration
! Device   : {{ hostname }}
! Topology : {{ topo_name }}
! OS       : Cisco IOS
! Generated: {{ generated_at }}
! WARNING  : Review all TODO comments before deploying.
! ============================================================
!
version 15.7
service timestamps debug datetime msec
service timestamps log datetime msec
service password-encryption
!
hostname {{ hostname }}
!
no ip domain-lookup
{% if domain_name %}ip domain-name {{ domain_name }}
{% endif %}!
{% for vrf in vrfs %}
ip vrf {{ vrf.name }}
 rd {{ vrf.rd }}
 route-target export {{ vrf.rt_export }}
 route-target import {{ vrf.rt_import }}
!
{% endfor %}
{% for iface in interfaces %}
interface {{ iface.name }}
 description {{ iface.description }}
{% if iface.vrf %} ip vrf forwarding {{ iface.vrf }}
{% endif %}
{% if iface.is_loopback and iface.ip_addr %} ip address {{ iface.ip_addr }} {{ iface.ip_mask }}
{% elif iface.ip_addr %} ip address {{ iface.ip_addr }} {{ iface.ip_mask }}
{% else %} no ip address
{% endif %}
{% if iface.vlan and not iface.is_loopback %} encapsulation dot1Q {{ iface.vlan }}
{% endif %}
{% if iface.mtu and not iface.is_loopback %} ip mtu {{ iface.mtu }}
{% endif %}
 no shutdown
!
{% endfor %}
{% for svi in svis %}
interface {{ svi.name }}
 description {{ svi.description }}
{% if svi.vrf %} ip vrf forwarding {{ svi.vrf }}
{% endif %}
{% if svi.ip_addr %} ip address {{ svi.ip_addr }} {{ svi.ip_mask }}
{% endif %}
 no shutdown
!
{% endfor %}
{% if ospf %}
router ospf {{ ospf.process_id }}
 router-id {{ ospf.router_id }}
{% for net in ospf.networks %}
 network {{ net.address }} {{ net.wildcard }} area {{ net.area }}
{% endfor %}
!
{% endif %}
{% if bgp and bgp.bfd %}
bfd interval 300 min_rx 300 multiplier 3
{% endif %}
{% if bgp %}
router bgp {{ bgp.asn }}
 bgp router-id {{ bgp.router_id }}
 bgp log-neighbor-changes
{% if bgp.local_pref %} bgp default local-preference {{ bgp.local_pref }}
{% endif %}
{% for neighbor in bgp.neighbors %}
 neighbor {{ neighbor.ip }} remote-as {{ neighbor.asn }}
 neighbor {{ neighbor.ip }} description {{ neighbor.description }}
{% if bgp.community %} neighbor {{ neighbor.ip }} send-community
{% endif %}
{% if bgp.bfd %} neighbor {{ neighbor.ip }} fall-over bfd{% endif %}
{% endfor %}
{% for vrf in vrfs %}
 address-family ipv4 vrf {{ vrf.name }}
  redistribute connected
  redistribute static
 exit-address-family
!
{% endfor %}
!
{% endif %}
{% if bgp and bgp.community %}
route-map DX-COMMUNITY permit 10
 set community {{ bgp.community }}
{% endif %}
ip ssh version 2
!
line con 0
 logging synchronous
!
line vty 0 4
 login local
 transport input ssh
!
end
"""

_IOS_SWITCH_TEMPLATE = """\
! ============================================================
! ICDEV(tm) Network Canvas -- Generated Configuration
! Device   : {{ hostname }}
! Topology : {{ topo_name }}
! OS       : Cisco IOS (Switch)
! Generated: {{ generated_at }}
! WARNING  : Review all TODO comments before deploying.
! ============================================================
!
version 15.7
service timestamps debug datetime msec
service timestamps log datetime msec
service password-encryption
!
hostname {{ hostname }}
!
no ip domain-lookup
!
spanning-tree mode rapid-pvst
spanning-tree extend system-id
!
{% for vlan_id in vlans %}
vlan {{ vlan_id }}
 name VLAN_{{ vlan_id }}
!
{% endfor %}
{% for vrf in vrfs %}
ip vrf {{ vrf.name }}
 rd {{ vrf.rd }}
 route-target export {{ vrf.rt_export }}
 route-target import {{ vrf.rt_import }}
!
{% endfor %}
{% for iface in interfaces %}
interface {{ iface.name }}
 description {{ iface.description }}
{% if iface.vrf %} ip vrf forwarding {{ iface.vrf }}
{% endif %}
{% if iface.is_loopback %}
{% if iface.ip_addr %} ip address {{ iface.ip_addr }} {{ iface.ip_mask }}{% endif %}
{% elif iface.is_routed %}
 no switchport
{% if iface.ip_addr %} ip address {{ iface.ip_addr }} {{ iface.ip_mask }}{% endif %}
{% elif iface.trunk %}
 switchport mode trunk
{% if iface.allowed_vlans %} switchport trunk allowed vlan {{ iface.allowed_vlans }}{% endif %}
{% elif iface.vlan %}
 switchport mode access
 switchport access vlan {{ iface.vlan }}
{% else %}
 switchport mode access
{% endif %}
{% if iface.mtu %} mtu {{ iface.mtu }}
{% endif %}
 no shutdown
!
{% endfor %}
{% for svi in svis %}
interface {{ svi.name }}
 description {{ svi.description }}
{% if svi.vrf %} ip vrf forwarding {{ svi.vrf }}
{% endif %}
{% if svi.ip_addr %} ip address {{ svi.ip_addr }} {{ svi.ip_mask }}
{% endif %}
 no shutdown
!
{% endfor %}
{% if ospf %}
ip routing
!
router ospf {{ ospf.process_id }}
 router-id {{ ospf.router_id }}
{% for net in ospf.networks %}
 network {{ net.address }} {{ net.wildcard }} area {{ net.area }}
{% endfor %}
!
{% endif %}
ip ssh version 2
!
line con 0
 logging synchronous
!
line vty 0 4
 login local
 transport input ssh
!
end
"""

_EOS_TEMPLATE = """\
! ============================================================
! ICDEV(tm) Network Canvas -- Generated Configuration
! Device   : {{ hostname }}
! Topology : {{ topo_name }}
! OS       : Arista EOS
! Generated: {{ generated_at }}
! WARNING  : Review all TODO comments before deploying.
! ============================================================
!
hostname {{ hostname }}
!
spanning-tree mode mstp
!
{% for vlan_id in vlans %}
vlan {{ vlan_id }}
   name VLAN_{{ vlan_id }}
!
{% endfor %}
{% for vrf in vrfs %}
vrf instance {{ vrf.name }}
   rd {{ vrf.rd }}
!
{% endfor %}
{% for iface in interfaces %}
interface {{ iface.name }}
   description {{ iface.description }}
{% if iface.vrf %}   vrf {{ iface.vrf }}
{% endif %}
{% if iface.is_loopback %}
{% if iface.ip_addr %}   ip address {{ iface.ip_addr }}/{{ iface.prefix_len }}{% endif %}
{% elif iface.is_routed %}
   no switchport
{% if iface.ip_addr %}   ip address {{ iface.ip_addr }}/{{ iface.prefix_len }}{% endif %}
{% elif iface.trunk %}
   switchport mode trunk
{% if iface.allowed_vlans %}   switchport trunk allowed vlan {{ iface.allowed_vlans }}{% endif %}
{% elif iface.vlan %}
   switchport mode access
   switchport access vlan {{ iface.vlan }}
{% endif %}
{% if iface.mtu %}   mtu {{ iface.mtu }}
{% endif %}
   no shutdown
!
{% endfor %}
{% for svi in svis %}
interface {{ svi.name }}
   description {{ svi.description }}
{% if svi.vrf %}   vrf {{ svi.vrf }}
{% endif %}
{% if svi.ip_addr %}   ip address {{ svi.ip_addr }}/{{ svi.prefix_len }}
{% endif %}
   no shutdown
!
{% endfor %}
{% if ospf %}
router ospf {{ ospf.process_id }}
   router-id {{ ospf.router_id }}
{% for net in ospf.networks %}
   network {{ net.address }}/{{ net.prefix_len }} area {{ net.area }}
{% endfor %}
!
{% endif %}
{% if bgp %}
router bgp {{ bgp.asn }}
   router-id {{ bgp.router_id }}
   bgp log-neighbor-changes
{% if bgp.local_pref %}   bgp default local-preference {{ bgp.local_pref }}
{% endif %}
{% for neighbor in bgp.neighbors %}
   neighbor {{ neighbor.ip }} remote-as {{ neighbor.asn }}
   neighbor {{ neighbor.ip }} description {{ neighbor.description }}
{% if bgp.bfd %}   neighbor {{ neighbor.ip }} bfd{% endif %}
{% endfor %}
!
{% endif %}
management api http-commands
   no shutdown
!
end
"""

_JUNOS_TEMPLATE = """\
## ============================================================
## ICDEV(tm) Network Canvas -- Generated Configuration
## Device   : {{ hostname }}
## Topology : {{ topo_name }}
## OS       : Juniper JunOS
## Generated: {{ generated_at }}
## WARNING  : Review all TODO comments before deploying.
## ============================================================
##
system {
    host-name {{ hostname }};
    services {
        ssh {
            protocol-version v2;
        }
        netconf {
            ssh;
        }
    }
    syslog {
        file messages {
            any notice;
            authorization info;
        }
    }
}
interfaces {
{% for iface in interfaces %}
    {{ iface.junos_name }} {
        description "{{ iface.description }}";
{% if iface.is_loopback and iface.ip_addr %}
        unit 0 {
            family inet {
                address {{ iface.ip_addr }}/{{ iface.prefix_len }};
            }
        }
{% elif iface.ip_addr %}
        unit 0 {
            family inet {
                address {{ iface.ip_addr }}/{{ iface.prefix_len }};
            }
        }
{% elif iface.vlan %}
        unit {{ iface.vlan }} {
            vlan-id {{ iface.vlan }};
            family inet;
        }
{% endif %}
    }
{% endfor %}
{% for svi in svis %}
    {{ svi.junos_name }} {
        description "{{ svi.description }}";
        unit 0 {
            family inet {
                address {{ svi.ip_addr }}/{{ svi.prefix_len }};
            }
        }
    }
{% endfor %}
}
{% if ospf %}
protocols {
    ospf {
        area {{ ospf.area }} {
{% for iface in interfaces %}
{% if iface.ip_addr and not iface.is_loopback %}
            interface {{ iface.junos_name }}.0 {
                interface-type p2p;
            }
{% endif %}
{% endfor %}
{% for iface in interfaces %}
{% if iface.is_loopback %}
            interface {{ iface.junos_name }}.0 {
                passive;
            }
{% endif %}
{% endfor %}
        }
    }
}
{% endif %}
{% if bgp %}
protocols {
    bgp {
        group ICDEV-PEERS {
            type external;
{% for neighbor in bgp.neighbors %}
            neighbor {{ neighbor.ip }} {
                peer-as {{ neighbor.asn }};
                description "{{ neighbor.description }}";
{% if bgp.local_pref %}
                import LOCAL-PREF-{{ bgp.local_pref }};
{% endif %}
{% if bgp.bfd %}            bfd-liveness-detection {
                minimum-interval 300;
                multiplier 3;
            }{% endif %}
            }
{% endfor %}
        }
    }
}
routing-options {
    autonomous-system {{ bgp.asn }};
{% if bgp.router_id %}
    router-id {{ bgp.router_id }};
{% endif %}
}
{% endif %}
{% for vrf in vrfs %}
routing-instances {
    {{ vrf.name }} {
        instance-type vrf;
        interface irb.{{ vrf.name }};
        route-distinguisher {{ vrf.rd }};
        vrf-target target:{{ vrf.rt_import }};
        vrf-export EXPORT-{{ vrf.name }};
        vrf-import IMPORT-{{ vrf.name }};
        protocols {
            bgp {
                group PEERS {
                    family inet unicast;
                }
            }
        }
    }
}
{% endfor %}
"""

_IOS_XR_TEMPLATE = """\
!! ============================================================
!! ICDEV(tm) Network Canvas -- Generated Configuration
!! Device   : {{ hostname }}
!! Topology : {{ topo_name }}
!! OS       : Cisco IOS-XR (Carrier / Service Provider)
!! Generated: {{ generated_at }}
!! WARNING  : Review all TODO comments before deploying.
!! ============================================================
!!
hostname {{ hostname }}
!!
logging console informational
logging buffered 2097152
!!
{% for iface in interfaces %}
interface {{ iface.name }}
 description {{ iface.description }}
{% if iface.is_loopback and iface.ip_addr %}
 ipv4 address {{ iface.ip_addr }} {{ iface.ip_mask }}
{% elif iface.ip_addr %}
 ipv4 address {{ iface.ip_addr }} {{ iface.ip_mask }}
{% endif %}
{% if iface.mtu and not iface.is_loopback %}
 mtu {{ iface.mtu }}
{% endif %}
 no shutdown
!
{% endfor %}
{% for svi in svis %}
interface {{ svi.name }}
 description {{ svi.description }}
{% if svi.vrf %} vrf {{ svi.vrf }}
{% endif %}
{% if svi.ip_addr %} ipv4 address {{ svi.ip_addr }} {{ svi.ip_mask }}
{% endif %}
 no shutdown
!
{% endfor %}
{% for vrf in vrfs %}
vrf {{ vrf.name }}
 rd {{ vrf.rd }}
 address-family ipv4 unicast
  route-target export {{ vrf.rt_export }}
  route-target import {{ vrf.rt_import }}
 !
!
{% endfor %}
{% if ospf %}
router ospf {{ ospf.process_id }}
 router-id {{ ospf.router_id }}
{% for net in ospf.networks %}
 area {{ net.area }} interface {{ net.address }}
{% endfor %}
!
{% endif %}
{% if bgp %}
router bgp {{ bgp.asn }}
 bgp router-id {{ bgp.router_id }}
 bgp log neighbor changes detail
{% if bgp.local_pref %}
 bgp bestpath as-path multipath-relax
{% endif %}
{% for neighbor in bgp.neighbors %}
 neighbor {{ neighbor.ip }}
  remote-as {{ neighbor.asn }}
  description {{ neighbor.description }}
  address-family ipv4 unicast
   route-policy IMPORT-{{ neighbor.asn }} in
   route-policy EXPORT-{{ neighbor.asn }} out
   soft-reconfiguration inbound always
  !
 !
{% endfor %}
{% for vrf in vrfs %}
 vrf {{ vrf.name }}
  rd {{ vrf.rd }}
  address-family ipv4 unicast
   redistribute connected
   redistribute static
  !
 !
{% endfor %}
!
{% endif %}
ssh server v2
ssh server logging
!
line default
 exec-timeout 30 0
!
end
"""

_NOKIA_SROS_TEMPLATE = """\
# ============================================================
# ICDEV(tm) Network Canvas -- Generated Configuration
# Device   : {{ hostname }}
# Topology : {{ topo_name }}
# OS       : Nokia SR OS (7750 SR / 7950 XRS)
# Generated: {{ generated_at }}
# WARNING  : Review all TODO comments before deploying.
# ============================================================

configure system
    name "{{ hostname }}"
    location "Generated by ICDEV NDC"
    contact "noc@example.com"
    security
        ssh
            server-cipher-list-v2
                cipher 190 name aes256-ctr
            exit
        exit
    exit
exit

{% for iface in interfaces %}
{% if iface.is_loopback and iface.ip_addr %}
configure router interface "system"
    address {{ iface.ip_addr }}/{{ iface.prefix_len }}
exit
{% elif iface.ip_addr %}
configure router interface "{{ iface.name }}"
    address {{ iface.ip_addr }}/{{ iface.prefix_len }}
    port 1/1/{{ loop.index }}
    description "{{ iface.description }}"
    no shutdown
exit
{% endif %}
{% endfor %}
{% if ospf %}
configure router ospf {{ ospf.process_id }}
    router-id {{ ospf.router_id }}
    area 0.0.0.{{ ospf.area }}
{% for net in ospf.networks %}
        interface "{{ net.address }}"
        exit
{% endfor %}
    exit
exit
{% endif %}
{% if bgp %}
configure router bgp
    router-id {{ bgp.router_id }}
    autonomous-system {{ bgp.asn }}
{% if bgp.local_pref %}
    local-preference {{ bgp.local_pref }}
{% endif %}
{% for neighbor in bgp.neighbors %}
    group "PEERS-{{ neighbor.asn }}"
        type external
        peer-as {{ neighbor.asn }}
        description "{{ neighbor.description }}"
        neighbor {{ neighbor.ip }}
        exit
    exit
{% endfor %}
    no shutdown
exit
{% endif %}
"""

_PANOS_TEMPLATE = """\
# ============================================================
# ICDEV(tm) Network Canvas -- Generated Configuration
# Device   : {{ hostname }}
# Topology : {{ topo_name }}
# OS       : Palo Alto PAN-OS
# Generated: {{ generated_at }}
# WARNING  : Review all TODO comments before deploying.
# ============================================================
#
set deviceconfig system hostname {{ hostname }}
set deviceconfig system timezone UTC
set deviceconfig system domain icdev.local
#
{% for vlan_id in vlans %}
set network vlan VLAN_{{ vlan_id }} {{ vlan_id }}
{% endfor %}
{% for iface in interfaces %}
{% if not iface.is_loopback %}
set network interface {{ iface.name }} layer3 ip {{ iface.ip_addr }}/{{ iface.prefix_len }}
set network interface {{ iface.name }} comment "{{ iface.description }}"
{% endif %}
{% endfor %}
{% for vrf in vrfs %}
set network virtual-router {{ vrf.name }} interface {{ interfaces[0].name }}
{% endfor %}
{% if ospf %}
set network virtual-router default protocol ospf enable yes
set network virtual-router default protocol ospf area {{ ospf.area }} type normal
{% for net in ospf.networks %}
set network virtual-router default protocol ospf area {{ ospf.area }} interface {{ net.address }}
{% endfor %}
{% endif %}
{% if bgp %}
set network virtual-router default protocol bgp enable yes
set network virtual-router default protocol bgp router-id {{ bgp.router_id }}
set network virtual-router default protocol bgp local-as {{ bgp.asn }}
{% for neighbor in bgp.neighbors %}
set network virtual-router default protocol bgp peer-group PEERS peer {{ neighbor.ip }} peer-as {{ neighbor.asn }}
set network virtual-router default protocol bgp peer-group PEERS peer {{ neighbor.ip }} connection-options multihop ttl 2
{% endfor %}
{% endif %}
set zone trust network layer3 {{ interfaces[0].name }}
set zone untrust network layer3 {{ interfaces[1].name }}
commit
"""

_FORTIOS_TEMPLATE = """\
# ============================================================
# ICDEV(tm) Network Canvas -- Generated Configuration
# Device   : {{ hostname }}
# Topology : {{ topo_name }}
# OS       : Fortinet FortiOS
# Generated: {{ generated_at }}
# WARNING  : Review all TODO comments before deploying.
# ============================================================
#
config system global
    set hostname {{ hostname }}
    set timezone 04
end
{% for vrf in vrfs %}
config vdom
    edit "{{ vrf.name }}"
next
end
{% endfor %}
{% for vlan_id in vlans %}
config system switch-interface
    edit "VLAN_{{ vlan_id }}"
        set vdom "root"
        set type vlan
        set vlanid {{ vlan_id }}
        set interface {{ interfaces[0].name }}
    next
end
{% endfor %}
{% for iface in interfaces %}
config system interface
    edit "{{ iface.name }}"
{% if iface.is_loopback %}
        set vdom "root"
        set ip {{ iface.ip_addr }} {{ iface.ip_mask }}
        set type loopback
{% elif iface.ip_addr %}
        set vdom "root"
        set ip {{ iface.ip_addr }} {{ iface.ip_mask }}
        set type physical
{% endif %}
        set description "{{ iface.description }}"
        set status up
    next
end
{% endfor %}
{% for svi in svis %}
config system interface
    edit "VLAN_{{ svi.vlan_id }}"
        set vdom "root"
        set ip {{ svi.ip_addr }} {{ svi.ip_mask }}
        set type vlan
        set vlanid {{ svi.vlan_id }}
        set interface {{ interfaces[0].name }}
    next
end
{% endfor %}
{% if ospf %}
config router ospf
    set router-id {{ ospf.router_id }}
    config area
        edit {{ ospf.area }}
        next
    end
{% for net in ospf.networks %}
    config ospf-interface
        edit "{{ net.address }}"
            set interface {{ interfaces[0].name }}
        next
    end
{% endfor %}
end
{% endif %}
{% if bgp %}
config router bgp
    set as {{ bgp.asn }}
    set router-id {{ bgp.router_id }}
{% for neighbor in bgp.neighbors %}
    config neighbor
        edit "{{ neighbor.ip }}"
            set remote-as {{ neighbor.asn }}
            set description "{{ neighbor.description }}"
            set bfd enable
        next
    end
{% endfor %}
end
{% endif %}
"""

_TEMPLATES: dict[str, str] = {
    "ios_router": _IOS_ROUTER_TEMPLATE,
    "ios_switch": _IOS_SWITCH_TEMPLATE,
    "eos": _EOS_TEMPLATE,
    "junos": _JUNOS_TEMPLATE,
    "ios_xr": _IOS_XR_TEMPLATE,
    "nokia_sros": _NOKIA_SROS_TEMPLATE,
    "panos": _PANOS_TEMPLATE,
    "fortios": _FORTIOS_TEMPLATE,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_hostname(label: str) -> str:
    """Convert a node label to a valid device hostname."""
    s = re.sub(r"[^A-Za-z0-9\-]", "-", label.strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:63] or "DEVICE"


def _parse_ip(ip_str: str) -> tuple[str, str, str, int] | None:
    """Parse 'a.b.c.d/prefix' or 'a.b.c.d mask' into (addr, mask, network, prefix_len).

    Returns None on failure.
    """
    if not ip_str:
        return None
    ip_str = ip_str.strip()
    try:
        iface = ipaddress.IPv4Interface(ip_str)
        addr = str(iface.ip)
        mask = str(iface.netmask)
        network = str(iface.network.network_address)
        prefix_len = iface.network.prefixlen
        # Wildcard for Cisco IOS
        wild_parts = [str(255 - int(o)) for o in mask.split(".")]
        wildcard = ".".join(wild_parts)
        return addr, mask, network, prefix_len, wildcard
    except (ValueError, AttributeError):
        return None


def _parse_ip_safe(ip_str: str) -> dict[str, Any]:
    """Return dict with ip_addr, ip_mask, network, prefix_len, wildcard or empty strings."""
    result = _parse_ip(ip_str)
    if result:
        addr, mask, network, prefix_len, wildcard = result
        return {
            "ip_addr": addr,
            "ip_mask": mask,
            "network": network,
            "prefix_len": prefix_len,
            "wildcard": wildcard,
        }
    return {"ip_addr": "", "ip_mask": "255.255.255.255", "network": "", "prefix_len": 32, "wildcard": "0.0.0.0"}  # nosec B104


def _ospf_networks_from_interfaces(interfaces: list[dict], area: str) -> list[dict]:
    """Build OSPF network statements from interface list."""
    nets = []
    for iface in interfaces:
        if iface.get("network") and iface.get("wildcard"):
            nets.append(
                {
                    "address": iface["network"],
                    "wildcard": iface["wildcard"],
                    "prefix_len": iface.get("prefix_len", 24),
                    "area": area,
                }
            )
    return nets


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _node_cfg(node: dict) -> dict:
    """Return the merged config dict for a node.

    graphToJSON() serializes configData under the key "config".
    Legacy or hand-crafted graphs may store it flat at the top level.
    This helper merges both so downstream code always reads from one place.
    """
    nested = node.get("config") or {}
    # Start with nested configData; overlay any top-level keys that are not
    # graph structure fields (id, label, type, x, y, config, group_id).
    _GRAPH_KEYS = frozenset({"id", "label", "type", "x", "y", "config", "group_id", "nodeType"})
    flat = {k: v for k, v in node.items() if k not in _GRAPH_KEYS}
    merged = {**flat, **nested}  # nested (configData) wins over flat
    return merged


def _edge_cfg(edge: dict) -> dict:
    """Return the merged config dict for an edge (link).

    graphToJSON() serializes edge configData under key "config" (added in this
    patch).  Legacy edges store everything at the top level.
    """
    nested = edge.get("config") or {}
    _EDGE_GRAPH_KEYS = frozenset({"id", "source", "target", "label", "protocol", "config", "cableData"})
    flat = {k: v for k, v in edge.items() if k not in _EDGE_GRAPH_KEYS}
    merged = {**flat, **nested}
    return merged


def _build_device_context(
    node: dict,
    all_edges: list[dict],
    node_map: dict[str, dict],
    topo_name: str,
    os_type: str,
) -> dict[str, Any]:
    """Build the Jinja2 template context for a single device node.

    Interface numbering:
      - Index 0 is always the management loopback (if node.ip is set).
      - Subsequent indices correspond to edges connected to this node,
        using the edge label/ip if available.
    """
    ntype = node.get("type", "") or node.get("nodeType", "")
    nid = node["id"]
    cfg = _node_cfg(node)
    hostname = _safe_hostname(cfg.get("hostname") or node.get("label") or nid)
    iface_gen = _IFACE_GEN.get(os_type, _ios_iface)

    # Collect connected edges
    connected_edges = [e for e in all_edges if e.get("source") == nid or e.get("target") == nid]

    interfaces: list[dict] = []
    vlan_set: set[int] = set()

    # ── Management loopback (from node.ip) ────────────────────────────────
    mgmt_ip_info = _parse_ip_safe(cfg.get("ip") or cfg.get("ip_address") or "")
    if mgmt_ip_info["ip_addr"]:
        lo_name = iface_gen(0, is_loopback=True)
        junos_lo = "lo0"
        interfaces.append(
            {
                "name": lo_name,
                "junos_name": junos_lo,
                "description": "Management Loopback",
                "is_loopback": True,
                "is_routed": False,
                "trunk": False,
                "allowed_vlans": "",
                "vlan": None,
                "vrf": cfg.get("vrf") or "",
                "mtu": None,
                **mgmt_ip_info,
            }
        )

    router_id = mgmt_ip_info.get("ip_addr") or ""

    # ── Per-edge interfaces ────────────────────────────────────────────────
    for idx, edge in enumerate(connected_edges):
        is_source = edge.get("source") == nid
        peer_id = edge.get("target") if is_source else edge.get("source")
        peer_node = node_map.get(peer_id, {})
        peer_label = peer_node.get("label") or peer_id or "peer"

        iface_idx = idx + 1  # 0 is reserved for loopback
        iface_name = iface_gen(iface_idx, is_loopback=False)
        junos_name = f"ge-0/0/{iface_idx}"

        ecfg = _edge_cfg(edge)
        edge_ip = ecfg.get("ip") or ecfg.get("ip_address") or ""
        ip_info = _parse_ip_safe(edge_ip)

        edge_vlan_raw = ecfg.get("vlan")
        edge_vlan = int(edge_vlan_raw) if edge_vlan_raw else None
        if edge_vlan:
            vlan_set.add(edge_vlan)

        edge_label = edge.get("label") or edge.get("protocol") or ecfg.get("protocol") or ""
        description = f"Link to {peer_label}"
        if edge_label:
            description = f"{edge_label} — {description}"

        is_trunk = bool(ecfg.get("trunk") or (not edge_vlan and not ip_info["ip_addr"]))
        is_routed = bool(ip_info["ip_addr"] and not edge_vlan)

        interfaces.append(
            {
                "name": iface_name,
                "junos_name": junos_name,
                "description": description,
                "is_loopback": False,
                "is_routed": is_routed,
                "trunk": is_trunk and not ip_info["ip_addr"],
                "allowed_vlans": "",
                "vlan": edge_vlan,
                "vrf": ecfg.get("vrf") or "",
                "mtu": int(ecfg.get("mtu")) if ecfg.get("mtu") else None,
                **ip_info,
            }
        )

    # ── VLAN from node-level property ─────────────────────────────────────
    node_vlan = cfg.get("vlan")
    if node_vlan:
        try:
            vlan_set.add(int(node_vlan))
        except (ValueError, TypeError):
            pass

    sorted_vlans = sorted(vlan_set)

    # ── VRF context ─────────────────────────────────────────────────────
    vrf_set: set[str] = set()
    for iface in interfaces:
        if iface.get("vrf"):
            vrf_set.add(iface["vrf"])
    node_vrf = cfg.get("vrf")
    if node_vrf:
        vrf_set.add(str(node_vrf))

    vrfs: list[dict] = []
    for vname in sorted(vrf_set):
        # Derive a simple RD/RT from VRF name hash for deterministic demo values
        rd = f"65000:{abs(hash(vname)) % 4096}"
        rt = f"65000:{abs(hash(vname)) % 4096}"
        vrfs.append({"name": vname, "rd": rd, "rt_import": rt, "rt_export": rt})

    # ── SVI context (VLAN interfaces on L3 switches) ───────────────────────
    svis: list[dict] = []
    if ntype in {"switch-l3", "router"} and sorted_vlans:
        # Generate an SVI per VLAN with a deterministic .1 gateway
        for vid in sorted_vlans:
            svi_ip = f"10.{(vid % 255)}.{(vid // 255) % 255}.1"
            svis.append({
                "vlan_id": vid,
                "name": f"Vlan{vid}" if os_type in ("ios_switch", "ios_router") else f"Vlan{vid}",
                "junos_name": f"irb.{vid}",
                "ip_addr": svi_ip,
                "ip_mask": "255.255.255.0",
                "prefix_len": 24,
                "vrf": cfg.get("vrf") or "",
                "description": f"SVI for VLAN {vid}",
            })

    # ── OSPF context ──────────────────────────────────────────────────────
    ospf_area_raw = cfg.get("ospf_area")
    ospf_ctx: dict | None = None
    if ospf_area_raw is not None and ospf_area_raw != "":
        area = str(ospf_area_raw)
        nets = _ospf_networks_from_interfaces(interfaces, area)
        ospf_ctx = {
            "process_id": 1,
            "router_id": router_id or "0.0.0.0",  # nosec B104
            "area": area,
            "networks": nets,
        }

    # ── BGP context ───────────────────────────────────────────────────────
    bgp_ctx: dict | None = None
    asn_raw = cfg.get("asn")
    if asn_raw:
        try:
            asn = int(asn_raw)
        except (ValueError, TypeError):
            asn = 0
        if asn:
            # Build neighbor list from peer nodes that also have ASNs
            neighbors = []
            for edge in connected_edges:
                is_src = edge.get("source") == nid
                peer_id = edge.get("target") if is_src else edge.get("source")
                peer = node_map.get(peer_id, {})
                peer_cfg = _node_cfg(peer)
                peer_asn_raw = peer_cfg.get("asn")
                ecfg2 = _edge_cfg(edge)
                edge_ip = ecfg2.get("ip") or ecfg2.get("ip_address") or ""
                ip_info = _parse_ip_safe(edge_ip)
                if peer_asn_raw and ip_info["ip_addr"]:
                    try:
                        peer_asn = int(peer_asn_raw)
                    except (ValueError, TypeError):
                        continue
                    neighbors.append(
                        {
                            "ip": ip_info["ip_addr"],
                            "asn": peer_asn,
                            "description": _safe_hostname(peer.get("label") or peer_id),
                        }
                    )

            local_pref_raw = cfg.get("local_pref")
            bgp_ctx = {
                "asn": asn,
                "router_id": router_id or "0.0.0.0",  # nosec B104
                "local_pref": int(local_pref_raw) if local_pref_raw else None,
                "community": cfg.get("community") or "",
                "med": int(cfg.get("med")) if cfg.get("med") else None,
                "bfd": bool(cfg.get("bfd") or cfg.get("bfd_enabled")),
                "neighbors": neighbors,
            }

    return {
        "hostname": hostname,
        "topo_name": topo_name,
        "generated_at": _now_utc(),
        "domain_name": "",
        "interfaces": interfaces,
        "vlans": sorted_vlans,
        "vrfs": vrfs,
        "svis": svis,
        "ospf": ospf_ctx,
        "bgp": bgp_ctx,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_config(ctx: dict, os_type: str) -> str:
    """Render a device config using the appropriate Jinja2 template."""
    template_str = _TEMPLATES.get(os_type, _IOS_ROUTER_TEMPLATE)

    if _JINJA2_AVAILABLE:
        # autoescape intentionally disabled: output is plaintext network config, not HTML.
        # Enabling autoescape would corrupt ACL syntax (e.g., <, >) in device configs.
        env = Environment(  # nosec B701
            loader=BaseLoader(),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            autoescape=False,
        )
        tmpl = env.from_string(template_str)
        return tmpl.render(**ctx)

    # Fallback: plain-text skeleton if Jinja2 not installed
    lines = [
        f"! Device: {ctx['hostname']}",
        f"! Topology: {ctx['topo_name']}",
        f"! Generated: {ctx['generated_at']}",
        f"! OS: {os_type}",
        "! INSTALL jinja2 for full config: pip install jinja2",
        "",
        f"hostname {ctx['hostname']}",
        "",
    ]
    for iface in ctx["interfaces"]:
        lines.append(f"interface {iface['name']}")
        lines.append(f" description {iface['description']}")
        if iface.get("ip_addr"):
            lines.append(f" ip address {iface['ip_addr']} {iface['ip_mask']}")
        lines.append(" no shutdown")
        lines.append("!")
    lines.append("end")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_device_configs(graph: dict, topo_name: str) -> dict[str, str]:
    """Generate per-device config files from a topology graph.

    Args:
        graph:     Dict with "nodes" and "edges" lists.
        topo_name: Topology name (used in config headers).

    Returns:
        Dict mapping filename (e.g. "core-router_ios.txt") to config text.
        Only configurable device types are included.
    """
    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])
    node_map: dict[str, dict] = {n["id"]: n for n in nodes}

    configs: dict[str, str] = {}
    name_counts: dict[str, int] = {}  # deduplicate filenames

    for node in nodes:
        ntype = node.get("type", "") or node.get("nodeType", "")
        if ntype not in _CONFIGURABLE_TYPES:
            continue

        # Determine OS type: check configData first, then default by device type
        cfg = _node_cfg(node)
        os_type = cfg.get("os") or _DEFAULT_OS.get(ntype, "ios_router")
        if os_type not in _TEMPLATES:
            os_type = "ios_router"

        ctx = _build_device_context(node, edges, node_map, topo_name, os_type)
        config_text = _render_config(ctx, os_type)

        # Build filename: <hostname>_<os_suffix>.txt
        _os_suffix = {
            "ios_router": "ios",
            "ios_switch": "ios",
            "eos": "eos",
            "junos": "junos",
            "ios_xr": "ios_xr",
            "nokia_sros": "sros",
            "panos": "panos",
            "fortios": "fortios",
        }.get(os_type, os_type)
        base = f"{ctx['hostname']}_{_os_suffix}.txt"

        # Deduplicate
        if base in name_counts:
            name_counts[base] += 1
            base = f"{ctx['hostname']}_{_os_suffix}_{name_counts[base]}.txt"
        else:
            name_counts[base] = 1

        configs[base] = config_text

    return configs


def generate_device_configs_zip(graph: dict, topo_name: str) -> bytes:
    """Generate a ZIP archive of all device config files.

    Returns:
        Bytes of the ZIP file content.
    """
    import io
    import zipfile

    configs = generate_device_configs(graph, topo_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, content in configs.items():
            zf.writestr(filename, content)
    return buf.getvalue()


def list_configurable_nodes(graph: dict) -> list[dict]:
    """Return metadata for all nodes that will receive a generated config.

    Useful for UI previews.
    """
    nodes = graph.get("nodes", [])
    result = []
    for node in nodes:
        ntype = node.get("type", "") or node.get("nodeType", "")
        if ntype not in _CONFIGURABLE_TYPES:
            continue
        cfg = _node_cfg(node)
        os_type = cfg.get("os") or _DEFAULT_OS.get(ntype, "ios_router")
        result.append(
            {
                "id": node["id"],
                "label": node.get("label", node["id"]),
                "type": ntype,
                "os_type": os_type,
                "hostname": _safe_hostname(cfg.get("hostname") or node.get("label") or node["id"]),
            }
        )
    return result
