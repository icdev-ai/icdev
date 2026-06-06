#!/usr/bin/env python3
"""Seed Dewie MX1003/MX304 demo data for realistic project page rendering."""
import sqlite3
import json
import uuid
import hashlib
from datetime import datetime, timezone

DB = sqlite3.connect("data/network_canvas.db")
DB.row_factory = sqlite3.Row
now = datetime.now(timezone.utc).isoformat()

# 1. Link topo-wam-42 to project
try:
    DB.execute(
        "INSERT INTO nc_project_topologies (project_id, topology_id) VALUES (?, ?)",
        ("proj-dewie-mx304", "topo-wam-42"),
    )
    print("Linked topo-wam-42 to project")
except sqlite3.IntegrityError:
    print("Already linked")

# 2. Create old device (Juniper MX1003 "Dewie") — EOL
DB.execute(
    """
    INSERT OR REPLACE INTO ni_devices
    (id, topology_id, node_id, label, device_type, vendor, model,
     firmware_version, eol_date, eos_date, purchase_date, purchase_cost,
     annual_maintenance_cost, replacement_cost, site, rack_location,
     criticality_score, downstream_count, notes, properties_json, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        "dev-dewie-mx1003",
        "topo-wam-42",
        "node-dewie-mx1003",
        "DEWIE-MX1003",
        "router",
        "Juniper",
        "MX1003",
        "20.4R3-S2.4",
        "2025-09-30",
        "2025-12-31",
        "2018-03-15",
        450000.0,
        38000.0,
        520000.0,
        "WAM-POP-ASH",
        "Rack-42-U15",
        0.92,
        24,
        "Primary edge router for NIPR trunk and DISA BCAP peering. Reaching EOL Q3 2025.",
        json.dumps({"role": "edge-router", "north_peer": "DISA-BCAP", "south_peer": "OWNED-SWITCH"}),
        now,
        now,
    ),
)
print("Created dev-dewie-mx1003")

# 3. Create new device (Juniper MX304 replacement)
DB.execute(
    """
    INSERT OR REPLACE INTO ni_devices
    (id, topology_id, node_id, label, device_type, vendor, model,
     firmware_version, eol_date, eos_date, purchase_date, purchase_cost,
     annual_maintenance_cost, replacement_cost, site, rack_location,
     criticality_score, downstream_count, notes, properties_json, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """,
    (
        "dev-dewie-mx304",
        "topo-wam-42",
        "node-dewie-mx304",
        "DEWIE-MX304",
        "router",
        "Juniper",
        "MX304",
        "23.2R1",
        None,
        None,
        None,
        520000.0,
        28000.0,
        0.0,
        "WAM-POP-ASH",
        "Rack-42-U16",
        0.0,
        0,
        "Replacement target for DEWIE-MX1003. Higher throughput, lower power, 2032 EOS.",
        json.dumps({"role": "edge-router", "replaces": "dev-dewie-mx1003"}),
        now,
        now,
    ),
)
print("Created dev-dewie-mx304")

# 4. Add realistic JunOS running config
config_text = """## Last changed: 2025-05-20 14:33:12 UTC by network-ops
## Router: DEWIE-MX1003 (Juniper MX1003)
## Site: WAM-POP-ASH, Rack-42-U15
## Role: Edge Router -- NIPR trunk + DISA BCAP eBGP

system {
    host-name DEWIE-MX1003;
    domain-name wam-pop-ash.mil;
    time-zone UTC;
    name-server {
        10.42.15.10;
        10.42.15.11;
    }
    ntp {
        server 10.42.15.20;
    }
    syslog {
        user * {
            any emergency;
        }
        host 10.42.15.30 {
            any info;
        }
    }
}

interfaces {
    ge-0/0/0 {
        description "NIPR Trunk -- North to DISA BCAP";
        unit 0 {
            family inet {
                address 10.100.42.1/30;
            }
        }
    }
    ge-0/0/1 {
        description "DISA BCAP eBGP Peer";
        unit 0 {
            family inet {
                address 10.100.42.5/30;
            }
        }
    }
    xe-0/1/0 {
        description "LAG Member 1 -- South to Owned Switch";
        gigether-options {
            802.3ad ae0;
        }
    }
    xe-0/1/1 {
        description "LAG Member 2 -- South to Owned Switch";
        gigether-options {
            802.3ad ae0;
        }
    }
    ae0 {
        description "South LAG -- Owned Switch Cluster";
        aggregated-ether-options {
            lacp {
                active;
                periodic fast;
            }
        }
        unit 0 {
            family inet {
                address 10.42.16.1/24;
            }
        }
    }
    ge-0/0/2 {
        description "ISP-ATT eBGP Peer";
        unit 0 {
            family inet {
                address 192.0.2.1/30;
            }
        }
    }
    lo0 {
        description "Loopback -- Router ID / Mgmt";
        unit 0 {
            family inet {
                address 10.42.255.1/32 {
                    primary;
                }
            }
        }
    }
    irb.42 {
        description "SVI for OOB VLAN 42";
        family inet {
            address 10.42.42.1/24;
        }
    }
}

routing-options {
    router-id 10.42.255.1;
    autonomous-system 65042;
    static {
        route 0.0.0.0/0 next-hop 10.100.42.2;
    }
}

protocols {
    bgp {
        group DISA-BCAP {
            type external;
            peer-as 65513;
            neighbor 10.100.42.6 {
                description "DISA BCAP eBGP";
                import [ DISA-IN DISA-DEFAULT ];
                export [ LOCAL-ROUTES DISA-OUT ];
            }
        }
        group ISP-ATT {
            type external;
            peer-as 7018;
            neighbor 192.0.2.2 {
                description "ISP-ATT eBGP";
                import [ ISP-IN DEFAULT-ONLY ];
                export [ LOCAL-ROUTES ISP-OUT ];
            }
        }
        group OWNED-SWITCH {
            type internal;
            local-address 10.42.255.1;
            neighbor 10.42.16.2 {
                description "Owned Switch iBGP";
                family inet {
                    unicast;
                }
            }
        }
    }
    ospf {
        area 0.0.0.0 {
            interface lo0.0 {
                passive;
            }
            interface ae0.0;
        }
    }
}

policy-options {
    policy-statement LOCAL-ROUTES {
        term local {
            from protocol [ direct static ospf ];
            then accept;
        }
    }
    policy-statement DISA-IN {
        term default {
            from {
                route-filter 0.0.0.0/0 exact;
            }
            then accept;
        }
        term reject {
            then reject;
        }
    }
    policy-statement DISA-OUT {
        term owned {
            from {
                route-filter 10.42.0.0/16 orlonger;
            }
            then accept;
        }
    }
}

firewall {
    filter EDGE-IN {
        term ALLOW-SSH {
            from {
                protocol tcp;
                destination-port 22;
            }
            then accept;
        }
        term DENY-TELNET {
            from {
                protocol tcp;
                destination-port 23;
            }
            then {
                syslog;
                discard;
            }
        }
        term ALLOW-BGP {
            from {
                protocol tcp;
                destination-port 179;
            }
            then accept;
        }
        term ALLOW-ICMP {
            from {
                protocol icmp;
            }
            then accept;
        }
        term DEFAULT-DENY {
            then {
                syslog;
                discard;
            }
        }
    }
}

## End of config"""

config_hash = hashlib.sha256(config_text.encode()).hexdigest()[:16]
config_id = str(uuid.uuid4())

DB.execute(
    """
    INSERT OR REPLACE INTO ni_device_configs
    (id, device_id, config_type, config_text, config_hash, source, version, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (config_id, "dev-dewie-mx1003", "running", config_text, config_hash, "ndc-seed", 1, now),
)
print("Added running config for dev-dewie-mx1003")

# 5. Add Dewie node to topo-wam-42 graph
topo_row = DB.execute("SELECT graph_json FROM topologies WHERE id=?", ("topo-wam-42",)).fetchone()
graph = json.loads(topo_row[0] if topo_row and topo_row[0] else '{"nodes":[], "edges":[]}')
nodes = graph.get("nodes", [])
edges = graph.get("edges", [])

has_dewie = any(n.get("id") == "node-dewie-mx1003" for n in nodes)
if not has_dewie:
    xs = [n.get("x", 0) for n in nodes]
    ys = [n.get("y", 0) for n in nodes]
    new_x = max(xs) + 120 if xs else 300
    new_y = max(ys) + 80 if ys else 300

    nodes.append(
        {
            "id": "node-dewie-mx1003",
            "label": "DEWIE-MX1003",
            "type": "router",
            "vendor": "Juniper",
            "model": "MX1003",
            "x": new_x,
            "y": new_y,
            "eol": "2025-09-30",
            "meta": {
                "site": "WAM-POP-ASH",
                "rack_location": "Rack-42-U15",
                "eol_date": "2025-09-30",
                "eos_date": "2025-12-31",
                "role": "edge-router",
                "criticality_score": 0.92,
                "mgmt_ip": "10.42.255.1",
                "bgp_asn": 65042,
                "bgp_session_count": 3,
            },
        }
    )
    edges.append(
        {
            "id": f"edge-dewie-isp-{len(edges)+1}",
            "source": "node-dewie-mx1003",
            "target": "dev-isp-att",
            "type": "wan",
            "bandwidth_gbps": 1.0,
            "label": "DEWIE-ISP-ATT",
            "meta": {
                "provider": "ATT",
                "circuit_type": "eBGP",
                "contract_expiry": "2026-03-15",
            },
        }
    )
    edges.append(
        {
            "id": f"edge-dewie-core-{len(edges)+1}",
            "source": "node-dewie-mx1003",
            "target": "dev-core-wam-pop-ash",
            "type": "circuit",
            "bandwidth_gbps": 10.0,
            "label": "DEWIE-CORE",
        }
    )
    print("Added DEWIE-MX1003 node + edges to graph")
else:
    print("DEWIE-MX1003 already in graph")

has_mx304 = any(n.get("id") == "node-dewie-mx304" for n in nodes)
if not has_mx304:
    nodes.append(
        {
            "id": "node-dewie-mx304",
            "label": "DEWIE-MX304",
            "type": "router",
            "vendor": "Juniper",
            "model": "MX304",
            "x": new_x + 80 if "new_x" in dir() else 400,
            "y": new_y + 60 if "new_y" in dir() else 350,
            "meta": {
                "site": "WAM-POP-ASH",
                "rack_location": "Rack-42-U16",
                "role": "edge-router",
                "replaces": "node-dewie-mx1003",
                "mgmt_ip": "10.42.255.2",
            },
        }
    )
    print("Added DEWIE-MX304 node to graph")

graph["nodes"] = nodes
graph["edges"] = edges
DB.execute("UPDATE topologies SET graph_json=? WHERE id=?", (json.dumps(graph), "topo-wam-42"))

DB.commit()
DB.close()
print("Done seeding Dewie data")
