# NQL Data Schema Reference

This document describes every entity type, collection, and field available in NQL. Use it to:
- Resolve which collection to query for a given natural-language intent.
- Verify that a field path is valid before emitting a query.
- Understand field types, nullability, and cardinality.

Nullability key: **R** = required (never null), **O** = optional (may be null or absent).

---

## 1. `network.devices`

Top-level device inventory. One row per managed network device.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable unique identifier (UUID or FQDN-based) |
| `hostname` | string | R | Device hostname (short or FQDN) |
| `fqdn` | string | O | Fully qualified domain name |
| `vendor` | string | R | Hardware vendor: `"cisco"`, `"juniper"`, `"arista"`, `"palo_alto"`, `"fortinet"`, etc. |
| `role` | string | R | Network role: `"core"`, `"distribution"`, `"access"`, `"edge"`, `"firewall"`, `"load_balancer"`, `"spine"`, `"leaf"`, `"wan_pe"`, `"wan_ce"`, `"branch_cpe"` |
| `os.type` | string | R | OS family: `"ios-xe"`, `"ios-xr"`, `"nxos"`, `"eos"`, `"junos"`, `"pan-os"`, `"fortios"` |
| `os.version` | string | R | OS version string (e.g., `"17.9.4"`) |
| `hardware.platform` | string | R | Platform model string (e.g., `"ASR9001"`, `"N9K-C9336C-FX2"`) |
| `hardware.serial` | string | O | Chassis serial number |
| `hardware.memory_mb` | integer | O | Total DRAM in MB |
| `hardware.cpu_pct` | float | O | Current CPU utilization (0–100) |
| `management.ip` | ip | O | Primary OOB/management IP address |
| `management.protocol` | string | O | `"ssh"`, `"netconf"`, `"restconf"`, `"snmp"` |
| `location.site` | string | O | Site or data-center code (e.g., `"DC1"`, `"LON-HUB"`) |
| `location.rack` | string | O | Rack identifier |
| `location.building` | string | O | Building identifier |
| `uptime_seconds` | integer | O | Seconds since last reload |
| `last_seen` | string | O | ISO-8601 timestamp of last successful poll |
| `tags` | list<string> | O | Arbitrary operator-assigned tags |

### Sub-object: `d.interfaces`

Array of interface summaries on the device. Full interface detail lives in `network.interfaces`.

| Field | Type | Description |
|-------|------|-------------|
| `d.interfaces.*.name` | string | Interface name (e.g., `"GigabitEthernet0/0/1"`) |
| `d.interfaces.*.status` | string | `"up"` or `"down"` |
| `d.interfaces.*.errors.in` | integer | Input error counter |
| `d.interfaces.*.errors.out` | integer | Output error counter |

### Sub-object: `d.bgp`

Summary of BGP sessions on the device.

| Field | Type | Description |
|-------|------|-------------|
| `d.bgp.router_id` | ip | BGP router ID |
| `d.bgp.local_as` | integer | Locally configured AS number |
| `d.bgp.peers.*.remote_as` | integer | Peer AS numbers |
| `d.bgp.peers.*.state` | string | Peer state |

---

## 2. `network.interfaces`

All physical and logical interfaces, one row per interface.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable interface identifier |
| `device` | string | R | Hostname of the owning device |
| `device_id` | string | R | Device UUID (use for joins on `network.devices.id`) |
| `name` | string | R | Interface name (e.g., `"Ethernet1/1"`, `"GigabitEthernet0/1"`) |
| `description` | string | O | Operator-configured description |
| `layer` | integer | R | `2` or `3` (L2 switchport or L3 routed) |
| `type` | string | R | `"physical"`, `"loopback"`, `"vlan"`, `"port_channel"`, `"tunnel"`, `"management"` |
| `status` | string | R | Operational status: `"up"` or `"down"` |
| `admin_status` | string | R | Administrative status: `"up"` or `"down"` |
| `speed_mbps` | integer | O | Negotiated or configured speed in Mbps |
| `duplex` | string | O | `"full"`, `"half"`, `"auto"` |
| `mtu` | integer | O | MTU in bytes |
| `ip.address` | ip | O | Primary IPv4 address |
| `ip.prefix_length` | integer | O | IPv4 prefix length |
| `ip.prefix` | prefix | O | Combined `address/prefix_length` |
| `ipv6.address` | ip | O | Primary IPv6 address |
| `ipv6.prefix_length` | integer | O | IPv6 prefix length |
| `mac_address` | string | O | MAC address (colon-separated hex) |
| `errors.in` | integer | O | Input error count |
| `errors.out` | integer | O | Output error count |
| `errors.crc` | integer | O | CRC / FCS error count |
| `counters.in_octets` | integer | O | Inbound bytes since last clear |
| `counters.out_octets` | integer | O | Outbound bytes since last clear |
| `counters.in_packets` | integer | O | Inbound packet count |
| `counters.out_packets` | integer | O | Outbound packet count |
| `counters.drops_in` | integer | O | Inbound drops |
| `counters.drops_out` | integer | O | Outbound drops |
| `switchport.mode` | string | O | `"access"`, `"trunk"`, `"routed"` |
| `switchport.access_vlan` | integer | O | Access VLAN ID (mode=access only) |
| `switchport.trunk_vlans` | list<integer> | O | Allowed VLANs (mode=trunk) |
| `vrf` | string | O | VRF name; `"global"` if in the default VRF |
| `last_flap` | string | O | ISO-8601 timestamp of last link-state change |

---

## 3. `network.links`

Layer-2 or layer-3 adjacencies between interfaces (cable / logical link level).

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable link identifier |
| `device_a` | string | R | Hostname of the A-end device |
| `interface_a` | string | R | Interface name on device A |
| `device_b` | string | R | Hostname of the B-end device |
| `interface_b` | string | R | Interface name on device B |
| `layer` | integer | R | `2` or `3` |
| `status` | string | R | `"up"` or `"down"` |
| `bandwidth_mbps` | integer | O | Link bandwidth in Mbps |
| `latency_ms` | float | O | Measured one-way latency in milliseconds |
| `vrf` | string | O | VRF for L3 links |
| `mpls_enabled` | bool | O | True if MPLS forwarding is active |
| `vlan` | integer | O | VLAN tag for L2 links |
| `discovery_source` | string | O | `"lldp"`, `"cdp"`, `"static"`, `"ospf"`, `"bgp"` |

---

## 4. `network.bgp.sessions`

BGP peer session table. One row per local-device ↔ peer-address pair.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable session identifier |
| `local_device` | string | R | Hostname of the local router |
| `local_as` | integer | R | Local AS number |
| `local_address` | ip | O | Source IP used for the session |
| `peer_address` | ip | R | Remote peer IP address |
| `peer_as` | integer | R | Remote AS number |
| `type` | string | R | `"ibgp"` or `"ebgp"` |
| `state` | string | R | BGP FSM state: `"Idle"`, `"Connect"`, `"Active"`, `"OpenSent"`, `"OpenConfirm"`, `"Established"` |
| `vrf` | string | O | VRF the session runs in |
| `address_families` | list<string> | O | Active AFs: `["ipv4-unicast", "ipv6-unicast", "vpnv4"]` |
| `prefixes_received` | integer | O | Number of prefixes received from peer |
| `prefixes_advertised` | integer | O | Number of prefixes sent to peer |
| `uptime_seconds` | integer | O | Session uptime (Established only) |
| `last_reset_reason` | string | O | Reason for the most recent session reset |
| `next_hop_self` | bool | O | Whether next-hop-self is configured |
| `route_reflector_client` | bool | O | True if peer is an RR client |
| `peer_group` | string | O | Peer-group name if assigned |

---

## 5. `network.bgp.routes`

BGP RIB (Routing Information Base). One row per prefix per peer per device.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `device` | string | R | Device where this RIB entry is held |
| `prefix` | prefix | R | IP prefix (CIDR notation) |
| `next_hop` | ip | R | BGP next-hop address |
| `peer_address` | ip | R | Learned from this peer |
| `peer_as` | integer | R | Source peer AS |
| `as_path` | list<integer> | O | AS-PATH sequence |
| `local_preference` | integer | O | LOCAL_PREF attribute |
| `med` | integer | O | MED (MULTI_EXIT_DISC) attribute |
| `communities` | list<string> | O | BGP community strings (e.g., `"65000:100"`) |
| `origin` | string | O | `"igp"`, `"egp"`, `"incomplete"` |
| `best` | bool | R | True if this is the best-path selection |
| `valid` | bool | R | True if route passes validity checks |
| `vrf` | string | O | VRF of the RIB |
| `address_family` | string | R | `"ipv4-unicast"`, `"ipv6-unicast"`, etc. |

---

## 6. `network.acls`

Access-control list definitions (containers).

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable ACL identifier |
| `device` | string | R | Device where the ACL is defined |
| `name` | string | R | ACL name (IOS) or number |
| `type` | string | R | `"standard"`, `"extended"`, `"ipv6"` |
| `applied_to` | list<string> | O | Interface names where ACL is applied |
| `direction` | string | O | `"inbound"`, `"outbound"`, or `"both"` |
| `rule_count` | integer | R | Total number of ACEs |

---

## 7. `network.acls.rules`

Individual ACE (Access Control Entry) rows.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable ACE identifier |
| `acl_id` | string | R | Parent ACL identifier |
| `acl_name` | string | R | Parent ACL name (denormalized for convenience) |
| `device` | string | R | Device where the ACL resides |
| `sequence` | integer | R | ACE sequence number |
| `action` | string | R | `"permit"` or `"deny"` |
| `protocol` | string | O | `"ip"`, `"tcp"`, `"udp"`, `"icmp"`, `"esp"`, `"ah"`, or protocol number |
| `source` | string | O | Source address / prefix / `"any"` / `"host <ip>"` |
| `source_port` | string | O | Source port or range (TCP/UDP) |
| `destination` | string | O | Destination address / prefix / `"any"` |
| `destination_port` | string | O | Destination port or range |
| `log` | bool | O | True if the `log` keyword is present |
| `established` | bool | O | True if TCP established flag is set |
| `remark` | string | O | Comment text (remark lines) |
| `hit_count` | integer | O | ACE hit counter (if available) |

---

## 8. `network.vlans`

VLAN definitions.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | integer | R | VLAN ID (1–4094) |
| `device` | string | R | Switch where the VLAN is defined |
| `name` | string | O | VLAN name |
| `state` | string | O | `"active"` or `"suspended"` |
| `member_interfaces` | list<string> | O | Access-port interface names in this VLAN |

---

## 9. `network.prefixes`

Layer-3 prefix/subnet inventory (static or derived from RIBs).

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable prefix identifier |
| `prefix` | prefix | R | CIDR-notation prefix |
| `length` | integer | R | Prefix-length (mask bits) |
| `address_family` | string | R | `"ipv4"` or `"ipv6"` |
| `vrf` | string | O | VRF name |
| `device` | string | O | Device this prefix is routed on (if applicable) |
| `next_hop` | ip | O | Next-hop for the prefix |
| `protocol` | string | O | `"connected"`, `"static"`, `"ospf"`, `"bgp"`, `"isis"` |
| `description` | string | O | Human-readable description |

---

## 10. `network.ospf.neighbors`

OSPF adjacency table.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `local_device` | string | R | Local router hostname |
| `local_interface` | string | R | Interface on which adjacency forms |
| `neighbor_id` | ip | R | OSPF Router ID of the neighbor |
| `neighbor_ip` | ip | R | Neighbor interface address |
| `state` | string | R | `"Down"`, `"Attempt"`, `"Init"`, `"2-Way"`, `"ExStart"`, `"Exchange"`, `"Loading"`, `"Full"` |
| `area` | string | R | OSPF area (e.g., `"0.0.0.0"`, `"0.0.0.10"`) |
| `priority` | integer | O | DR/BDR election priority |
| `dead_time_seconds` | integer | O | Remaining dead interval |
| `uptime_seconds` | integer | O | Adjacency uptime |

---

## 11. `network.isis.adjacencies`

IS-IS adjacency table.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `local_device` | string | R | Local router hostname |
| `local_interface` | string | R | Local interface |
| `neighbor_sysid` | string | R | Neighbor System ID |
| `neighbor_ip` | ip | O | Neighbor IP (if available) |
| `state` | string | R | `"Up"`, `"Init"`, `"Down"` |
| `level` | integer | R | `1` or `2` |
| `circuit_type` | string | O | `"point-to-point"`, `"broadcast"` |
| `uptime_seconds` | integer | O | Adjacency uptime |

---

## 12. `network.mpls.lsps`

MPLS Label-Switched Path table.

| Field | Type | N | Description |
|-------|------|---|-------------|
| `id` | string | R | Stable LSP identifier |
| `name` | string | O | LSP name (RSVP-TE / LDP label) |
| `src` | string | R | Ingress device hostname |
| `dst` | string | R | Egress device hostname |
| `state` | string | R | `"Up"`, `"Down"`, `"Standby"` |
| `signaling_protocol` | string | R | `"rsvp-te"`, `"ldp"`, `"segment-routing"` |
| `bandwidth_mbps` | integer | O | Reserved bandwidth |
| `active_path` | list<string> | O | Ordered list of hop device hostnames |
| `backup_path` | list<string> | O | FRR / standby path hops |
| `setup_priority` | integer | O | RSVP setup priority |
| `hold_priority` | integer | O | RSVP hold priority |

---

## 13. Path Query Return Type

`network.pathsbetween(...)` returns a list of path objects:

| Field | Type | Description |
|-------|------|-------------|
| `hops` | list<string> | Ordered device hostnames along the path |
| `links` | list<object> | Link objects for each hop (same schema as `network.links`) |
| `hop_count` | integer | Number of transit hops |
| `total_latency_ms` | float | Sum of per-link latency_ms (requires weight: link.latency_ms) |
| `total_bandwidth_mbps` | integer | Minimum bandwidth along path |
| `algorithm` | string | Algorithm used |

---

## 14. Common Join Keys

| Join | Key A | Key B |
|------|-------|-------|
| Devices → Interfaces | `d.id` | `iface.device_id` |
| Interfaces → Links | `iface.name + iface.device` | `link.interface_a + link.device_a` |
| Devices → BGP Sessions | `d.hostname` | `s.local_device` |
| ACLs → ACL Rules | `acl.id` | `rule.acl_id` |
| Devices → OSPF Neighbors | `d.hostname` | `n.local_device` |
| Devices → ISIS Adjacencies | `d.hostname` | `adj.local_device` |

---

## 15. Enum Value Reference

### `d.role`
`core`, `distribution`, `access`, `edge`, `firewall`, `load_balancer`, `spine`, `leaf`, `wan_pe`, `wan_ce`, `branch_cpe`, `out_of_band`, `management`

### `d.os.type`
`ios`, `ios-xe`, `ios-xr`, `nxos`, `eos`, `junos`, `pan-os`, `fortios`, `acos`, `cumulus`, `sonic`, `srlinux`, `vrp`

### `d.vendor`
`cisco`, `juniper`, `arista`, `palo_alto`, `fortinet`, `a10`, `f5`, `nokia`, `huawei`, `cumulus`, `dell`

### `bgp.session.state`
`Idle`, `Connect`, `Active`, `OpenSent`, `OpenConfirm`, `Established`

### `bgp.session.type`
`ibgp`, `ebgp`

### `ospf.neighbor.state`
`Down`, `Attempt`, `Init`, `2-Way`, `ExStart`, `Exchange`, `Loading`, `Full`

### `acl.rule.action`
`permit`, `deny`

### `interface.status` / `interface.admin_status`
`up`, `down`

### `interface.switchport.mode`
`access`, `trunk`, `routed`

### `link.discovery_source`
`lldp`, `cdp`, `static`, `ospf`, `bgp`, `manual`
