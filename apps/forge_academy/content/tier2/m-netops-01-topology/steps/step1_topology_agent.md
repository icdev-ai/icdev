---
ontology_id: icdev:mission:m-netops-01-topology:step:1
step_class: icdev:Lesson
---

# NetOps M01 — Network Topology Agent

## Mission Brief

Your network operations center receives raw device configuration dumps from routers and switches every 15 minutes. Right now, an analyst manually reads each dump to draw the topology map. You're replacing that analyst with a **Network Topology Agent** that parses configs, extracts adjacency data, and outputs a structured graph.

## What You'll Build

Three components wire together into `TopologyAgent`:

1. **`parse_device_config(config_str)`** — Parse a single device config string. Extract the device hostname, its interfaces (name + IP + subnet), and any neighbor relationships declared in CDP/LLDP neighbor sections.

2. **`build_topology(devices)`** — Given a list of parsed device dicts, build a topology graph. Return nodes (one per device) and edges (one per neighbor link, deduplicated).

3. **`TopologyAgent.run(raw_configs)`** — Orchestrate: parse each config, build the graph, flag any device that appears as a neighbor but has no config of its own (unreachable/unknown nodes).

## Data Contract

### Input to `parse_device_config`

A multiline string like:

```
hostname core-sw-01
!
interface GigabitEthernet0/1
 ip address 10.0.1.1 255.255.255.0
!
interface GigabitEthernet0/2
 ip address 10.0.2.1 255.255.255.252
!
cdp neighbor
 neighbor: edge-rtr-01 port: Gi0/2
 neighbor: access-sw-01 port: Gi0/1
```

### Output of `parse_device_config`

```python
{
    "hostname": "core-sw-01",
    "interfaces": [
        {"name": "GigabitEthernet0/1", "ip": "10.0.1.1", "subnet": "255.255.255.0"},
        {"name": "GigabitEthernet0/2", "ip": "10.0.2.1", "subnet": "255.255.255.252"},
    ],
    "neighbors": ["edge-rtr-01", "access-sw-01"]
}
```

### Output of `build_topology`

```python
{
    "nodes": ["core-sw-01", "edge-rtr-01", "access-sw-01"],
    "edges": [
        {"source": "core-sw-01", "target": "edge-rtr-01"},
        {"source": "core-sw-01", "target": "access-sw-01"},
    ]
}
```

Edges are **undirected** — if A lists B as neighbor and B lists A, store only one edge (alphabetical source < target).

### Output of `TopologyAgent.run`

```python
{
    "node_count": 3,
    "edge_count": 2,
    "topology": { ... },          # full graph from build_topology
    "unknown_nodes": ["edge-rtr-01"]  # neighbors with no config
}
```

## Implementation Tips

- Parse hostname with: find the line starting with `hostname ` and take the second token.
- Parse interfaces: lines starting with `interface ` open a block; `ip address` inside gives IP + mask.
- Parse neighbors: lines under `cdp neighbor` block containing `neighbor:` give the remote hostname.
- For `build_topology`, collect all node names first, then walk each device's neighbor list to produce edges. Use a set of frozensets to deduplicate bidirectional links.
- Unknown nodes: take the set of all neighbor names mentioned across all devices, subtract the set of device hostnames you parsed.

## Grader Contract

The auto-grader calls your functions with `SAMPLE_CONFIGS` (3 device configs, 3 edges, 1 unknown node). All assertions check specific field names and values — match the schema exactly.
