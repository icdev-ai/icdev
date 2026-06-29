# NQL → IQE Mapping Reference

Maps Forward Networks NQE collection paths and filter expressions to equivalent
ICDEV IQE (foreach/where/select) queries. Used by `FallbackNQEClient` when a
live NQE endpoint is unavailable.

---

## Mapping Table

| NQE Collection Path | IQE Equivalent |
|---|---|
| `network.devices` | `foreach d in network.devices select d.label, d.object_type` |
| `network.devices[platform.ostype]` | `foreach d in network.devices select d.label, d.object_type, d.os.type` |
| `network.devices[platform.osversion]` | `foreach d in network.devices select d.label, d.os.type, d.os.version` |
| `network.devices[platform.vendor]` | `foreach d in network.devices select d.label, d.vendor` |
| `network.devices[platform.hardware]` | `foreach d in network.devices select d.label, d.hardware.platform, d.hardware.serial` |
| `network.devices[management]` | `foreach d in network.devices select d.label, d.management.ip, d.management.protocol` |
| `network.devices[location]` | `foreach d in network.devices select d.label, d.location.site, d.location.rack, d.location.building` |
| `network.devices[config]` | `foreach d in network.devices select d.label, d.config` |
| `network.devices[uptime]` | `foreach d in network.devices select d.label, d.uptime_seconds` |
| `network.interfaces` | `foreach i in network.interfaces select i.device, i.name, i.status, i.ip.address` |
| `network.interfaces[errors]` | `foreach i in network.interfaces where i.errors.in > 0 or i.errors.out > 0 select i.device, i.name, i.errors.in, i.errors.out, i.errors.crc` |
| `network.interfaces[down]` | `foreach i in network.interfaces where i.status == "down" select i.device, i.name, i.admin_status, i.last_flap` |
| `network.interfaces[ip]` | `foreach i in network.interfaces where i.ip.address is not null select i.device, i.name, i.ip.address, i.ip.prefix_length, i.vrf` |
| `network.interfaces[counters]` | `foreach i in network.interfaces select i.device, i.name, i.counters.in_octets, i.counters.out_octets, i.counters.drops_in, i.counters.drops_out` |
| `network.interfaces[switchport]` | `foreach i in network.interfaces where i.switchport.mode is not null select i.device, i.name, i.switchport.mode, i.switchport.access_vlan, i.switchport.trunk_vlans` |
| `network.links` | `foreach l in network.links select l.device_a, l.interface_a, l.device_b, l.interface_b, l.status` |
| `network.links[down]` | `foreach l in network.links where l.status == "down" select l.device_a, l.interface_a, l.device_b, l.interface_b, l.discovery_source` |
| `network.links[mpls]` | `foreach l in network.links where l.mpls_enabled == true select l.device_a, l.interface_a, l.device_b, l.interface_b, l.vrf` |
| `network.bgp_sessions` | `foreach d in network.devices select d.label, d.config` |
| `network.bgp.sessions` | `foreach s in network.bgp.sessions select s.local_device, s.peer_address, s.peer_as, s.state, s.type` |
| `network.bgp.sessions[down]` | `foreach s in network.bgp.sessions where s.state != "Established" select s.local_device, s.peer_address, s.peer_as, s.state, s.last_reset_reason` |
| `network.bgp.sessions[ibgp]` | `foreach s in network.bgp.sessions where s.type == "ibgp" select s.local_device, s.peer_address, s.peer_as, s.state, s.prefixes_received` |
| `network.bgp.sessions[ebgp]` | `foreach s in network.bgp.sessions where s.type == "ebgp" select s.local_device, s.peer_address, s.peer_as, s.state, s.prefixes_received` |
| `network.bgp.routes` | `foreach r in network.bgp.routes where r.best == true select r.device, r.prefix, r.next_hop, r.peer_as, r.local_preference` |
| `network.bgp.routes[invalid]` | `foreach r in network.bgp.routes where r.valid == false select r.device, r.prefix, r.peer_address, r.peer_as` |
| `network.acls` | `foreach a in network.acls select a.device, a.name, a.type, a.rule_count, a.direction` |
| `network.acls.rules[deny]` | `foreach r in network.acls.rules where r.action == "deny" select r.device, r.acl_name, r.sequence, r.protocol, r.source, r.destination` |
| `network.acls.rules[permit]` | `foreach r in network.acls.rules where r.action == "permit" select r.device, r.acl_name, r.sequence, r.protocol, r.source, r.destination` |
| `network.vlans` | `foreach v in network.vlans select v.device, v.id, v.name, v.state` |
| `network.prefixes` | `foreach p in network.prefixes select p.prefix, p.address_family, p.vrf, p.protocol, p.device` |
| `network.prefixes[host]` | `foreach p in network.prefixes where p.length == 32 or p.length == 128 select p.prefix, p.address_family, p.vrf, p.device` |
| `network.ospf.neighbors` | `foreach n in network.ospf.neighbors select n.local_device, n.neighbor_id, n.neighbor_ip, n.state, n.area` |
| `network.ospf.neighbors[down]` | `foreach n in network.ospf.neighbors where n.state != "Full" select n.local_device, n.local_interface, n.neighbor_id, n.state, n.area` |
| `network.isis.adjacencies` | `foreach a in network.isis.adjacencies select a.local_device, a.neighbor_sysid, a.state, a.level` |
| `network.isis.adjacencies[down]` | `foreach a in network.isis.adjacencies where a.state != "Up" select a.local_device, a.local_interface, a.neighbor_sysid, a.state, a.level` |
| `network.mpls.lsps` | `foreach l in network.mpls.lsps select l.name, l.src, l.dst, l.state, l.signaling_protocol, l.bandwidth_mbps` |
| `network.mpls.lsps[down]` | `foreach l in network.mpls.lsps where l.state == "Down" select l.name, l.src, l.dst, l.signaling_protocol` |

---

## Filter Normalisation Rules

NQE square-bracket expressions are normalised to IQE `where` clauses by the
`FallbackNQEClient` before table lookup:

| NQE bracket expression | Normalised key suffix |
|---|---|
| `[platform.ostype == "ios-xe"]` | `[platform.ostype]` |
| `[platform.osversion contains "17."]` | `[platform.osversion]` |
| `[errors.in > 0]` | `[errors]` |
| `[status == "down"]` | `[down]` |
| `[state != "Established"]` | `[down]` (for BGP/OSPF/ISIS) |
| `[mpls_enabled == true]` | `[mpls]` |
| `[type == "ibgp"]` | `[ibgp]` |
| `[type == "ebgp"]` | `[ebgp]` |
| `[best == false]` or `[valid == false]` | `[invalid]` |
| `[action == "deny"]` | `[deny]` |
| `[action == "permit"]` | `[permit]` |
| `[length >= 32]` | `[host]` |

When no bracket expression is present the bare collection path is used (e.g.
`network.devices` → first row of the table above).

---

## Fallback Behaviour

When `FallbackNQEClient` cannot resolve a path via this table it falls back to:

1. **Heuristic IQE construction** — strips brackets, maps the root collection to
   a `foreach … in … select *` wildcard query, and passes the bracket contents
   as a raw `where` predicate string.
2. **LLM translation** — if `ICDEV_LLM_NQE_TRANSLATE=true` in `.env`, the
   router invokes the configured LLM to produce an IQE query from the NQE
   expression.
3. **Empty result with diagnostic** — returns `{"rows": [], "error": "unmapped",
   "nql": "<original>", "iqe": null}`.

---

## Usage Example

```python
from tools.network.nqe_client import FallbackNQEClient

client = FallbackNQEClient()

# Direct collection path
result = client.run_query("network.devices[platform.ostype]")
# → {"rows": [...], "iqe": "foreach d in ...", "source": "local_mapping"}

# With an explicit network_id scope
result = client.run_query("network.interfaces[errors]", network_id="topo-abc123")
```
