# NQL Grammar Reference

NQL (Network Query Language) is a declarative, read-only query language for interrogating network topology, device state, interface metrics, routing tables, and policy objects. It is inspired by LINQ and Cypher and is designed for translation from natural-language questions about network infrastructure.

---

## 1. Top-Level Query Structure

```
<query> ::= <foreach-clause> [<where-clause>] <select-clause>
          | <path-query>
          | <aggregate-query>
          | <join-query>
```

Every query is either a **collection traversal** (foreach/where/select), a **path query**, or an **aggregate**.

---

## 2. foreach … in … select

The primary query form. Iterates over a named collection, optionally filters rows, and projects a result.

```nql
foreach <alias> in <collection>
[where <predicate>]
select <projection>
```

### 2.1 Collection Sources

| Source | Returns |
|--------|---------|
| `network.devices` | All devices (routers, switches, firewalls, etc.) |
| `network.interfaces` | All physical and logical interfaces |
| `network.links` | All layer-2 / layer-3 adjacencies |
| `network.bgp.sessions` | BGP peer sessions |
| `network.bgp.routes` | BGP RIB entries |
| `network.acls` | Access-control lists |
| `network.acls.rules` | Individual ACE entries within ACLs |
| `network.vlans` | VLAN definitions |
| `network.prefixes` | IP prefix/subnet objects |
| `network.ospf.neighbors` | OSPF adjacency table |
| `network.isis.adjacencies` | IS-IS adjacency table |
| `network.mpls.lsps` | MPLS label-switched paths |

### 2.2 select Projection

```nql
-- single field
select d.hostname

-- multiple fields
select { d.hostname, d.os.version, d.role }

-- aliased fields
select { name: d.hostname, version: d.os.version }

-- computed field
select { d.hostname, error_rate: d.interfaces.errors.in / d.interfaces.counters.in_packets }
```

---

## 3. where Predicates

Predicates are boolean expressions evaluated per row. Operators:

| Operator | Meaning |
|----------|---------|
| `==` | Equality (string, number, bool) |
| `!=` | Inequality |
| `<`, `<=`, `>`, `>=` | Numeric / lexicographic comparison |
| `in [...]` | Set membership |
| `not in [...]` | Set non-membership |
| `contains` | Substring or list containment |
| `starts_with` | String prefix |
| `ends_with` | String suffix |
| `matches` | Regex match |
| `is null` | Null / missing field check |
| `is not null` | Non-null check |
| `and`, `or`, `not` | Logical connectives |

### 3.1 Examples

```nql
where d.os.type == "ios-xe"
where d.role in ["core", "distribution"]
where iface.status == "up" and iface.errors.out > 0
where session.state != "Established"
where prefix.length >= 24
where acl.name matches "^INBOUND.*"
where d.vendor is not null
```

### 3.2 Nested Field Predicates

Dotted paths traverse object trees:

```nql
where d.hardware.platform starts_with "ASR"
where d.os.version contains "17.3"
where iface.ip.address is not null
```

---

## 4. Nested Field Access

Fields are accessed via dot notation. Arrays are automatically flattened when iterating:

```nql
d.hostname                  -- top-level string
d.os.type                   -- nested object field
d.interfaces[0].name        -- index into array
d.interfaces.*.status       -- wildcard: all interface statuses
d.bgp.peers.*.remote_as     -- all remote AS numbers across peers
d.location.site.building    -- deep nesting
```

### 4.1 Array Flattening

When a path crosses an array, subsequent traversal applies to all elements:

```nql
foreach d in network.devices
select d.interfaces.*.ip.address   -- returns list of all IPs on all interfaces
```

---

## 5. Path Queries

Path queries find graph-traversal routes between network nodes.

### 5.1 Syntax

```nql
network.pathsbetween(
    src: <node-selector>,
    dst: <node-selector>,
    [via: <constraint>],
    [max_hops: <integer>],
    [algorithm: "shortest" | "all" | "disjoint"],
    [weight: <field-path>]
)
```

### 5.2 Node Selectors

```nql
-- by hostname
src: device("core-rtr-01")

-- by IP address
src: device(ip: "10.0.0.1")

-- by role
src: device(role: "edge")

-- by prefix (first device owning prefix)
src: prefix("192.168.10.0/24")
```

### 5.3 Constraints

```nql
-- traverse only via MPLS-enabled links
via: link.mpls_enabled == true

-- avoid a specific device
via: not device("maintenance-rtr-01")

-- constrain to a VRF
via: link.vrf == "PROD"
```

### 5.4 Full Example

```nql
network.pathsbetween(
    src: device("dc1-spine-01"),
    dst: device("dc2-spine-01"),
    algorithm: "shortest",
    max_hops: 8,
    weight: link.latency_ms
)
```

Returns a list of path objects: `{ hops: [...], total_latency: float, hop_count: int }`.

---

## 6. Built-in Functions

### 6.1 Aggregation Functions

```nql
count(<expr>)           -- count of non-null values
distinct(<field>)       -- deduplicated list
sum(<numeric-field>)    -- numeric total
avg(<numeric-field>)    -- arithmetic mean
min(<numeric-field>)    -- minimum
max(<numeric-field>)    -- maximum
first(<field>)          -- first value in iteration order
collect(<field>)        -- array of all values
```

Used in `select` clause:

```nql
foreach d in network.devices
select { total: count(d), platforms: distinct(d.hardware.platform) }
```

### 6.2 String Functions

```nql
lower(<str>)            -- lowercase
upper(<str>)            -- uppercase
trim(<str>)             -- strip whitespace
split(<str>, <delim>)   -- array of substrings
concat(<str>, ...)      -- join strings
length(<str>)           -- character count
```

### 6.3 Network Functions

```nql
cidr_contains(<prefix>, <ip>)         -- true if IP is within prefix
cidr_overlap(<prefix1>, <prefix2>)    -- true if prefixes overlap
ip_version(<ip>)                      -- returns 4 or 6
mask_length(<prefix>)                 -- /N integer
network_address(<prefix>)             -- base address
broadcast_address(<prefix>)           -- last address
```

### 6.4 Conditional Functions

```nql
coalesce(<a>, <b>, ...)   -- first non-null argument
if(<cond>, <then>, <else>)
```

---

## 7. Join Pattern

Joins correlate two collections on a shared key.

```nql
foreach <a> in <collection-A>
join <b> in <collection-B> on <a.key> == <b.key>
[where <predicate>]
select <projection>
```

### 7.1 Join Types

```nql
join          -- inner join (only matched rows)
left join     -- all rows from A, null B fields when no match
```

### 7.2 Example

```nql
foreach d in network.devices
join iface in network.interfaces on d.id == iface.device_id
where iface.status == "up"
select { device: d.hostname, interface: iface.name, ip: iface.ip.address }
```

---

## 8. Group By

Aggregate after grouping by one or more fields:

```nql
foreach <alias> in <collection>
[where <predicate>]
group by <field> [, <field>]
select { <group-key-field>, <aggregate-expr> }
```

```nql
foreach d in network.devices
group by d.os.type
select { os: d.os.type, count: count(d) }
```

---

## 9. Order By / Limit

```nql
foreach d in network.devices
where d.role == "access"
select { d.hostname, d.uptime_seconds }
order by d.uptime_seconds asc
limit 10
```

---

## 10. Subqueries

A subquery returns a scalar or list used in the enclosing predicate or projection:

```nql
-- scalar subquery in where
foreach d in network.devices
where count(
    foreach iface in d.interfaces where iface.status == "down" select iface
) > 0
select d.hostname

-- inline list subquery in select
foreach d in network.devices
select {
    d.hostname,
    down_interfaces: [
        foreach iface in d.interfaces
        where iface.status == "down"
        select iface.name
    ]
}
```

---

## 11. Variables and Let

Bind intermediate expressions to a name within a query:

```nql
let <name> = <expr>
foreach ...
```

```nql
let threshold = 1000
foreach iface in network.interfaces
where iface.errors.in > threshold
select { iface.device, iface.name, iface.errors.in }
```

---

## 12. Comments

```nql
-- single-line comment

/* multi-line
   comment */
```

---

## 13. Type System

| NQL Type | Description | Example literal |
|----------|-------------|-----------------|
| `string` | UTF-8 text | `"ios-xe"` |
| `integer` | 64-bit int | `65000` |
| `float` | 64-bit float | `0.5` |
| `bool` | true/false | `true` |
| `ip` | IPv4 or IPv6 address | `"10.0.0.1"` |
| `prefix` | CIDR notation | `"10.0.0.0/24"` |
| `list<T>` | Ordered collection | `["a", "b"]` |
| `null` | Absent / unknown | `null` |

---

## 14. Operator Precedence (high to low)

1. Dot field access, `[]` index, function call
2. Unary `not`, unary `-`
3. `*`, `/`, `%`
4. `+`, `-`
5. `<`, `<=`, `>`, `>=`, `contains`, `starts_with`, `ends_with`, `matches`
6. `==`, `!=`, `in`, `not in`, `is null`, `is not null`
7. `and`
8. `or`
