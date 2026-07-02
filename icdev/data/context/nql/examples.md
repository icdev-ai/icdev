# NQL Worked Examples

Each example includes the natural-language intent, the NQL query, and a brief explanation of the key constructs used.

---

## 1. All Devices Running a Specific OS Type

**Intent:** List every device running IOS-XE.

```nql
foreach d in network.devices
where d.os.type == "ios-xe"
select { d.hostname, d.os.version, d.role, d.location.site }
```

*Uses equality predicate on a nested field (`d.os.type`).*

---

## 2. Devices by OS Version (Exact)

**Intent:** Find all devices on IOS-XE 17.6.4.

```nql
foreach d in network.devices
where d.os.type == "ios-xe" and d.os.version == "17.6.4"
select { d.hostname, d.management.ip }
```

---

## 3. Devices on Outdated OS Versions

**Intent:** Find IOS-XE devices NOT on the approved version list.

```nql
let approved = ["17.9.4", "17.12.1", "17.12.2"]

foreach d in network.devices
where d.os.type == "ios-xe" and d.os.version not in approved
select { d.hostname, d.os.version, d.role }
order by d.os.version asc
```

---

## 4. BGP Session State — All Non-Established Sessions

**Intent:** Show all BGP peer sessions not in Established state.

```nql
foreach s in network.bgp.sessions
where s.state != "Established"
select {
    device: s.local_device,
    peer_ip: s.peer_address,
    peer_as: s.peer_as,
    state: s.state,
    last_reset: s.last_reset_reason
}
```

---

## 5. BGP Sessions by Peer AS

**Intent:** List all sessions peered with AS 64512.

```nql
foreach s in network.bgp.sessions
where s.peer_as == 64512
select { s.local_device, s.peer_address, s.state, s.prefixes_received }
```

---

## 6. BGP Sessions with High Prefix Count

**Intent:** Flag eBGP sessions receiving more than 800,000 prefixes (full-table risk).

```nql
foreach s in network.bgp.sessions
where s.type == "ebgp" and s.prefixes_received > 800000
select { s.local_device, s.peer_address, s.peer_as, s.prefixes_received }
order by s.prefixes_received desc
```

---

## 7. Interface Error Counters — Input Errors

**Intent:** Find interfaces with non-zero input errors.

```nql
foreach iface in network.interfaces
where iface.errors.in > 0
select {
    device: iface.device,
    name: iface.name,
    errors_in: iface.errors.in,
    drops_in: iface.counters.drops_in
}
order by iface.errors.in desc
```

---

## 8. Interface Error Counters — Output Errors Above Threshold

**Intent:** Find interfaces where output errors exceed 100 in the last polling cycle.

```nql
let threshold = 100

foreach iface in network.interfaces
where iface.errors.out > threshold and iface.status == "up"
select {
    iface.device,
    iface.name,
    iface.errors.out,
    iface.speed_mbps
}
```

---

## 9. Interfaces with High CRC Error Rate

**Intent:** Compute CRC error rate and surface links above 0.01%.

```nql
foreach iface in network.interfaces
where iface.counters.in_packets > 0
  and (iface.errors.crc / iface.counters.in_packets) > 0.0001
select {
    iface.device,
    iface.name,
    crc_rate: iface.errors.crc / iface.counters.in_packets,
    iface.errors.crc,
    iface.counters.in_packets
}
order by crc_rate desc
limit 20
```

---

## 10. ACL Permit Analysis — All Permit Rules for a Protocol

**Intent:** Show all ACL rules that permit TCP traffic.

```nql
foreach rule in network.acls.rules
where rule.action == "permit" and rule.protocol == "tcp"
select {
    acl_name: rule.acl_name,
    sequence: rule.sequence,
    src: rule.source,
    dst: rule.destination,
    dst_port: rule.destination_port
}
order by rule.acl_name asc, rule.sequence asc
```

---

## 11. ACL Deny Analysis — Any Rule Denying a Specific Subnet

**Intent:** Find ACL rules that deny traffic from 10.10.0.0/16.

```nql
foreach rule in network.acls.rules
where rule.action == "deny"
  and cidr_contains("10.10.0.0/16", rule.source)
select {
    rule.acl_name,
    rule.sequence,
    rule.source,
    rule.destination,
    rule.protocol
}
```

---

## 12. ACL Rules Without a Log Action (Security Gap)

**Intent:** Surface deny rules missing the `log` keyword (audit gap).

```nql
foreach rule in network.acls.rules
where rule.action == "deny" and rule.log == false
select { rule.acl_name, rule.sequence, rule.source, rule.destination }
```

---

## 13. Shortest Path Between Two Devices

**Intent:** Find the shortest path from `dc1-leaf-01` to `dc2-leaf-01`.

```nql
network.pathsbetween(
    src: device("dc1-leaf-01"),
    dst: device("dc2-leaf-01"),
    algorithm: "shortest",
    max_hops: 10
)
```

Returns `{ hops: [...devices...], hop_count: int, total_latency_ms: float }`.

---

## 14. All Paths (Multipath) Between Edge Routers

**Intent:** Enumerate all ECMP paths between two edge routers.

```nql
network.pathsbetween(
    src: device("edge-rtr-01"),
    dst: device("edge-rtr-02"),
    algorithm: "all",
    max_hops: 6
)
```

---

## 15. Path Constrained Through MPLS Core

**Intent:** Shortest path that must traverse MPLS-enabled links only.

```nql
network.pathsbetween(
    src: device("branch-pe-01"),
    dst: device("branch-pe-02"),
    algorithm: "shortest",
    via: link.mpls_enabled == true,
    weight: link.latency_ms
)
```

---

## 16. Devices Grouped by Vendor

**Intent:** Count devices per vendor.

```nql
foreach d in network.devices
group by d.vendor
select { vendor: d.vendor, count: count(d) }
order by count desc
```

---

## 17. Devices Grouped by Role

**Intent:** Show a breakdown of device counts by network role.

```nql
foreach d in network.devices
group by d.role
select { role: d.role, count: count(d), platforms: distinct(d.hardware.platform) }
```

---

## 18. Devices Grouped by Site and OS Type

**Intent:** Per-site OS type distribution.

```nql
foreach d in network.devices
group by d.location.site, d.os.type
select {
    site: d.location.site,
    os_type: d.os.type,
    count: count(d)
}
order by d.location.site asc, count desc
```

---

## 19. Join Devices to Interfaces — Devices with Any Down Interface

**Intent:** Find devices that have at least one interface in Down state.

```nql
foreach d in network.devices
join iface in network.interfaces on d.id == iface.device_id
where iface.status == "down" and iface.admin_status == "up"
select {
    d.hostname,
    d.role,
    down_interface: iface.name,
    iface.description
}
```

---

## 20. Join BGP Sessions to Devices — Edge Devices with No Established Peers

**Intent:** Find edge devices where every BGP session is down.

```nql
foreach d in network.devices
where d.role == "edge"
  and count(
      foreach s in network.bgp.sessions
      where s.local_device == d.hostname and s.state == "Established"
      select s
  ) == 0
select { d.hostname, d.location.site, d.management.ip }
```

---

## 21. OSPF Neighbors Not in Full State

**Intent:** Detect OSPF adjacencies stuck in non-Full states.

```nql
foreach n in network.ospf.neighbors
where n.state != "Full"
select {
    n.local_device,
    n.local_interface,
    n.neighbor_id,
    n.neighbor_ip,
    n.state,
    n.area
}
```

---

## 22. Interfaces with No IP Address (Unaddressed L3 Ports)

**Intent:** Find routed ports missing an IP assignment.

```nql
foreach iface in network.interfaces
where iface.layer == 3 and iface.ip.address is null and iface.status == "up"
select { iface.device, iface.name, iface.description }
```

---

## 23. Prefix Overlap Detection

**Intent:** Find any two prefixes in the routing table that overlap.

```nql
foreach p1 in network.prefixes
join p2 in network.prefixes on p1.id != p2.id
where cidr_overlap(p1.prefix, p2.prefix) and p1.vrf == p2.vrf
select {
    prefix_a: p1.prefix,
    prefix_b: p2.prefix,
    vrf: p1.vrf
}
```

---

## 24. Devices Missing a Management IP

**Intent:** Flag devices with no out-of-band management address.

```nql
foreach d in network.devices
where d.management.ip is null
select { d.hostname, d.role, d.location.site, d.vendor }
```

---

## 25. VLAN Membership — All Access Ports in VLAN 100

**Intent:** List every access-mode interface assigned to VLAN 100.

```nql
foreach iface in network.interfaces
where iface.switchport.mode == "access"
  and iface.switchport.access_vlan == 100
select {
    iface.device,
    iface.name,
    iface.description,
    iface.status
}
```

---

## 26. Top-N Interfaces by Traffic Volume

**Intent:** Find the 10 busiest interfaces by inbound octets.

```nql
foreach iface in network.interfaces
where iface.counters.in_octets is not null
select {
    iface.device,
    iface.name,
    iface.counters.in_octets,
    iface.speed_mbps,
    utilization_pct: (iface.counters.in_octets * 8 / iface.speed_mbps) / 1000000
}
order by iface.counters.in_octets desc
limit 10
```

---

## 27. IS-IS Adjacencies by Level

**Intent:** Summarize IS-IS adjacencies grouped by level.

```nql
foreach adj in network.isis.adjacencies
group by adj.level
select {
    level: adj.level,
    count: count(adj),
    up_count: count(if(adj.state == "Up", adj, null))
}
```

---

## 28. MPLS LSP Health Check

**Intent:** Find MPLS LSPs not in operational-up state.

```nql
foreach lsp in network.mpls.lsps
where lsp.state != "Up"
select {
    lsp.name,
    lsp.src,
    lsp.dst,
    lsp.state,
    lsp.signaling_protocol,
    lsp.bandwidth_mbps
}
```

---

## 29. Devices by Hardware Platform Substring

**Intent:** Find all ASR 9000 series routers.

```nql
foreach d in network.devices
where d.hardware.platform starts_with "ASR9"
select { d.hostname, d.hardware.platform, d.hardware.serial, d.location.site }
```

---

## 30. Dual-Stack Interface Audit

**Intent:** Find interfaces with both IPv4 and IPv6 addresses configured.

```nql
foreach iface in network.interfaces
where iface.ip.address is not null and iface.ipv6.address is not null
select {
    iface.device,
    iface.name,
    ipv4: iface.ip.address,
    ipv6: iface.ipv6.address
}
```
