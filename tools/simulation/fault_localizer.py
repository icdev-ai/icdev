# CUI // SP-CTI
"""Fault Localization Engine for TFW TROUBLESHOOT mode.

Pure functions — no Flask dependency, no DB side effects.

Entry point: localize_fault(symptom_text, canvas_type, graph=None) -> dict

Returns:
  fault_category      : primary symptom category (e.g. "auth_failure")
  all_categories      : all detected categories
  suspect_hops        : ranked list of suspect nodes
  root_causes         : ranked root causes with canvas-specific context and evidence
  sub_diagram_mermaid : Mermaid flowchart with suspect nodes styled red
  summary_text        : human-readable diagnosis narrative
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Symptom classification
# ---------------------------------------------------------------------------

_SYMPTOM_PATTERNS: dict[str, list[str]] = {
    "auth_failure": [
        "auth", "authentication", "401", "403", "token", "credential",
        "saml", "oauth", "jwt", "kerberos", "login", "access denied",
        "forbidden", "unauthorized", "sso", "idp", "oidc", "ldap",
    ],
    "tls_failure": [
        "tls", "ssl", "mtls", "certificate", "cert", "handshake",
        "x509", "ca ", "trust", "verify", "chain", "expired cert",
        "self-signed", "untrusted", "pkix",
    ],
    "network_drop": [
        "packet drop", "dropped", "firewall", "blocked", "acl", "deny",
        "unreachable", "no route", "ping fail", "packet loss", "black hole",
        "traffic drop", "filtered", "block", "icmp unreachable",
    ],
    "routing_failure": [
        "routing", "route", "bgp", "ospf", "path", "forward", "table",
        "redistribute", "prefix", "advertis", "withdraw", "flap",
        "convergence", "missing route",
    ],
    "event_delivery_failure": [
        "event", "consumer", "lag", "dlq", "dead letter", "queue",
        "kafka", "message", "offset", "partition", "not reaching",
        "not received", "broker", "topic", "pubsub", "sqs", "sns",
        "rabbitmq", "amqp", "nats", "subscriber",
    ],
    "latency_issue": [
        "latency", "slow", "timeout", "performance", "delay",
        "high latency", "bottleneck", "throughput", "congestion", "rtt",
    ],
    "mesh_failure": [
        "service mesh", "sidecar", "envoy", "istio", "linkerd", "proxy",
        "control plane", "data plane", "xds", "pilot", "injection",
        "istio", "istiod",
    ],
}


def classify_symptom(text: str) -> list[str]:
    """Return detected symptom categories from free-form symptom text.

    Returns at least one category; defaults to ['network_drop'] if nothing matches.
    """
    lower = text.lower()
    detected: list[str] = []
    for category, keywords in _SYMPTOM_PATTERNS.items():
        if any(kw in lower for kw in keywords):
            detected.append(category)
    return detected if detected else ["network_drop"]


# ---------------------------------------------------------------------------
# Root-cause profiles (canvas × fault_category)
# ---------------------------------------------------------------------------

_FAULT_PROFILES: dict[str, dict[str, list[dict[str, Any]]]] = {
    "ndc": {
        "auth_failure": [
            {
                "rank": 1,
                "root_cause": "Firewall ACL blocking RADIUS/TACACS+ port 1812/1813 between NAS and AAA server",
                "hop": "Firewall",
                "evidence": "show ip access-lists | include 1812; test aaa auth",
            },
            {
                "rank": 2,
                "root_cause": "AAA server shared-secret mismatch between NAS and RADIUS server",
                "hop": "AAA Server",
                "evidence": "RADIUS server logs for Access-Reject with Error-Cause 202",
            },
            {
                "rank": 3,
                "root_cause": "VPN headend certificate expired — IKEv2/IPSec Phase 1 auth failing",
                "hop": "VPN Gateway",
                "evidence": "show crypto pki certificates | include validity; verify NTP sync",
            },
            {
                "rank": 4,
                "root_cause": "NAT policy translating source IP — AAA server IP whitelist mismatch",
                "hop": "NAT Device",
                "evidence": "show ip nat translations; verify AAA server permit list",
            },
        ],
        "network_drop": [
            {
                "rank": 1,
                "root_cause": "Firewall ACL implicit deny — new subnet not covered by permit rule",
                "hop": "Firewall",
                "evidence": "show ip access-lists; watch deny counter increments",
            },
            {
                "rank": 2,
                "root_cause": "Routing table black hole — missing return route for destination segment",
                "hop": "Core Router",
                "evidence": "show ip route <dest>; traceroute to identify the losing hop",
            },
            {
                "rank": 3,
                "root_cause": "BGP route withdrawn — peer session flap removed the prefix",
                "hop": "BGP Peer",
                "evidence": "show bgp summary; show bgp neighbors | include flap",
            },
            {
                "rank": 4,
                "root_cause": "MTU mismatch — DF-bit set frames dropped at a lower-MTU link",
                "hop": "WAN Link",
                "evidence": "ping <dst> size 1500 df-bit; check interface MTU on transit hops",
            },
        ],
        "routing_failure": [
            {
                "rank": 1,
                "root_cause": "BGP AS_PATH filter rejecting advertisement — neighbor route-map missing",
                "hop": "BGP Peer",
                "evidence": "show route-map; show bgp neighbors <ip> advertised-routes",
            },
            {
                "rank": 2,
                "root_cause": "Route redistribution loop between OSPF and BGP — distribute-list absent",
                "hop": "Redistribution Point",
                "evidence": "show ip route | count; BGP table growing unexpectedly?",
            },
            {
                "rank": 3,
                "root_cause": "Missing 'network' statement in BGP — local prefix not originated",
                "hop": "BGP Speaker",
                "evidence": "show bgp; verify network statements match RIB entries",
            },
            {
                "rank": 4,
                "root_cause": "BGP hold-timer expiry with no BFD — slow convergence on link failure",
                "hop": "BGP Session",
                "evidence": "Enable BFD on BGP neighbors; show bgp neighbors | include hold",
            },
        ],
        "tls_failure": [
            {
                "rank": 1,
                "root_cause": "TLS inspection proxy re-signing with enterprise CA not trusted by client",
                "hop": "TLS Inspection Proxy",
                "evidence": "Inspect client cert chain; verify enterprise CA in client trust store",
            },
            {
                "rank": 2,
                "root_cause": "Incomplete certificate chain — missing intermediate CA on server",
                "hop": "Load Balancer",
                "evidence": "openssl s_client -connect <host>:443; verify full chain present",
            },
            {
                "rank": 3,
                "root_cause": "SNI not forwarded through load balancer — wildcard cert hostname mismatch",
                "hop": "Load Balancer",
                "evidence": "Check LB TLS passthrough vs. termination mode; SNI forwarding config",
            },
            {
                "rank": 4,
                "root_cause": "Certificate expired on firewall or load balancer — silent TLS reset",
                "hop": "Firewall",
                "evidence": "show ssl; check certificate expiry on all edge devices",
            },
        ],
        "latency_issue": [
            {
                "rank": 1,
                "root_cause": "WAN uplink congestion — no QoS policy for priority application traffic",
                "hop": "WAN Edge",
                "evidence": "show interfaces | include output drops; check interface utilization",
            },
            {
                "rank": 2,
                "root_cause": "OSPF cost misconfigured — traffic taking suboptimal high-latency path",
                "hop": "Core Router",
                "evidence": "show ip ospf interface; verify cost matches actual link bandwidth",
            },
            {
                "rank": 3,
                "root_cause": "Firewall CPU saturation — stateful inspection queue backlog",
                "hop": "Firewall",
                "evidence": "show processes cpu sorted; check connection table limits",
            },
            {
                "rank": 4,
                "root_cause": "ECMP hashing imbalance — majority of flows on a single link",
                "hop": "Load Balance Point",
                "evidence": "show ip cef exact-route; verify ECMP hash seed / tuple",
            },
        ],
    },
    "sdc": {
        "auth_failure": [
            {
                "rank": 1,
                "root_cause": "OIDC/SAML IdP unreachable — NetworkPolicy blocking egress from service namespace",
                "hop": "Identity Provider",
                "evidence": "curl from pod to IdP FQDN; kubectl get netpol -n <ns>",
            },
            {
                "rank": 2,
                "root_cause": "mTLS SPIFFE/SVID CN/SAN mismatch — service identity not matching policy",
                "hop": "Service Mesh Control Plane",
                "evidence": "istioctl proxy-config cert <pod>; verify SVID SAN format",
            },
            {
                "rank": 3,
                "root_cause": "OAuth client_secret rotation not propagated — credentials revoked",
                "hop": "API Gateway",
                "evidence": "OAuth introspection endpoint; IdP audit log for secret rotation events",
            },
            {
                "rank": 4,
                "root_cause": "JWKS cache stale after token signing key rotation — JWT verify fails",
                "hop": "Service Consumer",
                "evidence": "Check JWKS endpoint reachability; token cache TTL; force key refresh",
            },
        ],
        "tls_failure": [
            {
                "rank": 1,
                "root_cause": "mTLS cert not rotated within validity window — SPIRE/cert-manager backoff",
                "hop": "Envoy Sidecar",
                "evidence": "istioctl proxy-config secret <pod>; cert expiry < 24h?",
            },
            {
                "rank": 2,
                "root_cause": "Root CA rotation incomplete — trust bundle out of sync across mesh nodes",
                "hop": "Service Mesh Trust Domain",
                "evidence": "kubectl get secret istio-ca-secret; check trust bundle broadcast lag",
            },
            {
                "rank": 3,
                "root_cause": "Certificate SAN missing service FQDN — hostname validation fails at peer",
                "hop": "Service Certificate",
                "evidence": "openssl x509 -text | grep SAN; verify FQDN matches service name",
            },
            {
                "rank": 4,
                "root_cause": "OCSP responder unreachable in air-gapped environment — CRL check timeout",
                "hop": "PKI Validation Path",
                "evidence": "Check OCSP responder reachability; consider stapling or disable OCSP",
            },
        ],
        "mesh_failure": [
            {
                "rank": 1,
                "root_cause": "istiod/linkerd-control-plane unreachable — sidecar xDS push timeout",
                "hop": "Service Mesh Control Plane",
                "evidence": "kubectl get pod -n istio-system; istioctl proxy-status",
            },
            {
                "rank": 2,
                "root_cause": "Sidecar injection webhook not enabled for namespace — traffic bypasses mesh",
                "hop": "Mutating Webhook",
                "evidence": "kubectl get ns <name> --show-labels; istio-injection=enabled?",
            },
            {
                "rank": 3,
                "root_cause": "Pilot xDS push timeout — stale routing config on sidecar",
                "hop": "Pilot (xDS Server)",
                "evidence": "istioctl proxy-status; check SYNC status for affected pods",
            },
            {
                "rank": 4,
                "root_cause": "Envoy sidecar OOM restart — memory limit too low for connection table",
                "hop": "Envoy Sidecar",
                "evidence": "kubectl describe pod | grep OOM; adjust sidecar resource limits",
            },
        ],
        "network_drop": [
            {
                "rank": 1,
                "root_cause": "Kubernetes NetworkPolicy deny-all default — new service not in allow list",
                "hop": "NetworkPolicy",
                "evidence": "kubectl get netpol -A; test with temporary allow-all to isolate",
            },
            {
                "rank": 2,
                "root_cause": "Istio AuthorizationPolicy missing allow rule for new service",
                "hop": "Istio AuthorizationPolicy",
                "evidence": "kubectl get authorizationpolicy -A; istioctl analyze",
            },
            {
                "rank": 3,
                "root_cause": "Zero-trust deny-all policy not updated for new workload SPIFFE identity",
                "hop": "Zero Trust Policy Engine",
                "evidence": "Check SPIFFE workload identity in policy; new SVID registered?",
            },
            {
                "rank": 4,
                "root_cause": "Cilium identity map not updated — new pod label missing from network policy",
                "hop": "Cilium / eBPF Agent",
                "evidence": "cilium endpoint list; cilium policy get; hubble observe",
            },
        ],
    },
    "eda": {
        "event_delivery_failure": [
            {
                "rank": 1,
                "root_cause": "Consumer group lag: max.poll.interval.ms exceeded — rebalance loop triggered",
                "hop": "Consumer Group",
                "evidence": "kafka-consumer-groups.sh --describe; lag > 0 and growing?",
            },
            {
                "rank": 2,
                "root_cause": "Poison pill message blocking partition — consumer exception prevents offset advance",
                "hop": "Partition / DLQ",
                "evidence": "Consumer error logs; DLQ topic for failed messages; skip offset if safe",
            },
            {
                "rank": 3,
                "root_cause": "Broker ACL: SASL/SCRAM consumer credentials expired — READ permission denied",
                "hop": "Kafka Broker",
                "evidence": "kafka-acls.sh --list; broker logs for AUTHORIZATION_FAILED",
            },
            {
                "rank": 4,
                "root_cause": "Partition rebalance storm — consumer pod restarts causing join/leave thrashing",
                "hop": "Group Coordinator",
                "evidence": "Broker logs for 'Rebalancing group'; check consumer pod restart count",
            },
        ],
        "auth_failure": [
            {
                "rank": 1,
                "root_cause": "SASL/SCRAM credentials expired — password rotation not applied to consumer secret",
                "hop": "Kafka Authentication",
                "evidence": "K8s secret for KAFKA_PASSWORD; broker auth logs; test with kafkacat",
            },
            {
                "rank": 2,
                "root_cause": "Kafka ACL missing READ permission for consumer group on target topic",
                "hop": "Topic ACL",
                "evidence": "kafka-acls.sh --list --topic <name> --group <group>",
            },
            {
                "rank": 3,
                "root_cause": "Schema Registry authorization failure — new schema version rejected",
                "hop": "Schema Registry",
                "evidence": "Schema Registry audit log; verify consumer group auth scope",
            },
            {
                "rank": 4,
                "root_cause": "Broker mTLS client certificate expired for mutual TLS authentication",
                "hop": "Broker mTLS",
                "evidence": "ssl.keystore.location certificate expiry; renew and rolling-restart brokers",
            },
        ],
        "latency_issue": [
            {
                "rank": 1,
                "root_cause": "Hot partition: uneven key distribution causing single partition overloaded",
                "hop": "Partition Distribution",
                "evidence": "kafka-log-dirs.sh; compare partition sizes; change partitioner strategy",
            },
            {
                "rank": 2,
                "root_cause": "Consumer heartbeat timeout — poll interval too long for session.timeout.ms",
                "hop": "Consumer Heartbeat",
                "evidence": "heartbeat.interval.ms vs session.timeout.ms ratio; reduce poll interval",
            },
            {
                "rank": 3,
                "root_cause": "Broker disk I/O saturation — log compaction and retention running at peak",
                "hop": "Broker Storage",
                "evidence": "Broker I/O metrics; schedule retention/compaction outside peak hours",
            },
            {
                "rank": 4,
                "root_cause": "Large batch accumulation — head-of-line blocking on low-priority topics",
                "hop": "Producer Batch",
                "evidence": "linger.ms and batch.size config; consider per-topic priority queues",
            },
        ],
    },
}

# Canvas alias map — route non-profiled canvas types to the closest profile
_CANVAS_ALIAS: dict[str, str] = {
    "ddc": "ndc",
    "bdc": "sdc",
    "pdc": "eda",
    "odc": "ndc",
    "idc": "ndc",
    "qdc": "ndc",
    "mdc": "ndc",
}

_CANVAS_DISPLAY: dict[str, str] = {
    "ndc": "Network Design Canvas",
    "sdc": "Security Design Canvas",
    "eda": "Event-Driven Architecture",
    "ddc": "Data Design Canvas",
    "bdc": "Boundary Design Canvas",
    "pdc": "Pipeline Design Canvas",
    "odc": "Observability Design Canvas",
    "idc": "Infrastructure Design Canvas",
    "qdc": "Quality Design Canvas",
    "mdc": "Migration Design Canvas",
}


# ---------------------------------------------------------------------------
# Canonical hop paths (canvas × fault_category)
# Each hop: {id, label, suspect}
# suspect=True → drawn red in Mermaid
# ---------------------------------------------------------------------------

_HOP_PATHS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "ndc": {
        "auth_failure": [
            {"id": "A", "label": "User Agent", "suspect": False},
            {"id": "B", "label": "Edge Router", "suspect": False},
            {"id": "C", "label": "Firewall (ACL)", "suspect": True},
            {"id": "D", "label": "AAA Server", "suspect": True},
            {"id": "E", "label": "Auth Database", "suspect": True},
        ],
        "network_drop": [
            {"id": "A", "label": "Source Host", "suspect": False},
            {"id": "B", "label": "Access Switch", "suspect": False},
            {"id": "C", "label": "Firewall (Deny)", "suspect": True},
            {"id": "D", "label": "Core Router", "suspect": True},
            {"id": "E", "label": "BGP Peer", "suspect": True},
            {"id": "F", "label": "Destination", "suspect": False},
        ],
        "routing_failure": [
            {"id": "A", "label": "BGP Speaker", "suspect": False},
            {"id": "B", "label": "Route Map", "suspect": True},
            {"id": "C", "label": "BGP Peer", "suspect": True},
            {"id": "D", "label": "Route Reflector", "suspect": True},
            {"id": "E", "label": "CSP Gateway", "suspect": False},
        ],
        "tls_failure": [
            {"id": "A", "label": "Client", "suspect": False},
            {"id": "B", "label": "TLS Proxy", "suspect": True},
            {"id": "C", "label": "Load Balancer", "suspect": True},
            {"id": "D", "label": "Backend Service", "suspect": False},
        ],
        "latency_issue": [
            {"id": "A", "label": "Client", "suspect": False},
            {"id": "B", "label": "WAN Edge", "suspect": True},
            {"id": "C", "label": "Firewall", "suspect": True},
            {"id": "D", "label": "Core Router", "suspect": True},
            {"id": "E", "label": "Destination", "suspect": False},
        ],
        "mesh_failure": [
            {"id": "A", "label": "Source Service", "suspect": False},
            {"id": "B", "label": "Firewall", "suspect": True},
            {"id": "C", "label": "Routing Domain", "suspect": True},
            {"id": "D", "label": "Target Service", "suspect": False},
        ],
        "event_delivery_failure": [
            {"id": "A", "label": "Event Producer", "suspect": False},
            {"id": "B", "label": "Firewall (Port)", "suspect": True},
            {"id": "C", "label": "Message Broker", "suspect": True},
            {"id": "D", "label": "Consumer Endpoint", "suspect": True},
        ],
    },
    "sdc": {
        "auth_failure": [
            {"id": "A", "label": "Service A", "suspect": False},
            {"id": "B", "label": "API Gateway", "suspect": True},
            {"id": "C", "label": "Identity Provider", "suspect": True},
            {"id": "D", "label": "Service B", "suspect": False},
        ],
        "tls_failure": [
            {"id": "A", "label": "Client Service", "suspect": False},
            {"id": "B", "label": "Envoy Sidecar", "suspect": True},
            {"id": "C", "label": "Istio Control Plane", "suspect": True},
            {"id": "D", "label": "Server Sidecar", "suspect": True},
            {"id": "E", "label": "Target Service", "suspect": False},
        ],
        "mesh_failure": [
            {"id": "A", "label": "Workload Pod", "suspect": False},
            {"id": "B", "label": "Mutating Webhook", "suspect": True},
            {"id": "C", "label": "Pilot / istiod", "suspect": True},
            {"id": "D", "label": "Envoy Sidecar", "suspect": True},
            {"id": "E", "label": "Downstream Service", "suspect": False},
        ],
        "network_drop": [
            {"id": "A", "label": "Source Service", "suspect": False},
            {"id": "B", "label": "NetworkPolicy", "suspect": True},
            {"id": "C", "label": "AuthorizationPolicy", "suspect": True},
            {"id": "D", "label": "Cilium / eBPF", "suspect": True},
            {"id": "E", "label": "Target Service", "suspect": False},
        ],
        "routing_failure": [
            {"id": "A", "label": "Service Client", "suspect": False},
            {"id": "B", "label": "Service Mesh", "suspect": True},
            {"id": "C", "label": "Virtual Service", "suspect": True},
            {"id": "D", "label": "Service Endpoint", "suspect": False},
        ],
        "latency_issue": [
            {"id": "A", "label": "Client Service", "suspect": False},
            {"id": "B", "label": "Envoy Sidecar", "suspect": True},
            {"id": "C", "label": "Control Plane", "suspect": True},
            {"id": "D", "label": "Target Service", "suspect": False},
        ],
        "event_delivery_failure": [
            {"id": "A", "label": "Event Source", "suspect": False},
            {"id": "B", "label": "Service Mesh Policy", "suspect": True},
            {"id": "C", "label": "Event Bus", "suspect": True},
            {"id": "D", "label": "Consumer Service", "suspect": True},
        ],
    },
    "eda": {
        "event_delivery_failure": [
            {"id": "A", "label": "Producer", "suspect": False},
            {"id": "B", "label": "Kafka Topic", "suspect": False},
            {"id": "C", "label": "Partition", "suspect": True},
            {"id": "D", "label": "Consumer Group", "suspect": True},
            {"id": "E", "label": "Dead Letter Queue", "suspect": True},
        ],
        "auth_failure": [
            {"id": "A", "label": "Producer/Consumer", "suspect": False},
            {"id": "B", "label": "SASL Auth", "suspect": True},
            {"id": "C", "label": "Kafka Broker", "suspect": True},
            {"id": "D", "label": "Schema Registry", "suspect": True},
            {"id": "E", "label": "Topic", "suspect": False},
        ],
        "latency_issue": [
            {"id": "A", "label": "Producer", "suspect": True},
            {"id": "B", "label": "Partition Leader", "suspect": True},
            {"id": "C", "label": "Broker Storage", "suspect": True},
            {"id": "D", "label": "Consumer", "suspect": True},
        ],
        "network_drop": [
            {"id": "A", "label": "Producer", "suspect": False},
            {"id": "B", "label": "Network / Firewall", "suspect": True},
            {"id": "C", "label": "Kafka Broker", "suspect": True},
            {"id": "D", "label": "Consumer", "suspect": False},
        ],
        "tls_failure": [
            {"id": "A", "label": "Client", "suspect": False},
            {"id": "B", "label": "Broker mTLS", "suspect": True},
            {"id": "C", "label": "Schema Registry TLS", "suspect": True},
            {"id": "D", "label": "Consumer Service", "suspect": False},
        ],
        "routing_failure": [
            {"id": "A", "label": "Producer", "suspect": False},
            {"id": "B", "label": "Topic Router", "suspect": True},
            {"id": "C", "label": "Partition Assigner", "suspect": True},
            {"id": "D", "label": "Consumer", "suspect": False},
        ],
        "mesh_failure": [
            {"id": "A", "label": "Event Source", "suspect": False},
            {"id": "B", "label": "Sidecar Proxy", "suspect": True},
            {"id": "C", "label": "Service Mesh", "suspect": True},
            {"id": "D", "label": "Consumer Service", "suspect": False},
        ],
    },
}


# ---------------------------------------------------------------------------
# Graph-topology augmentation (optional)
# ---------------------------------------------------------------------------

# Node type keywords → fault categories they are suspect for
_TYPE_TO_FAULT: dict[str, list[str]] = {
    "firewall": ["network_drop", "auth_failure", "tls_failure", "latency_issue"],
    "aws-nfw": ["network_drop", "auth_failure"],
    "az-fw": ["network_drop", "auth_failure"],
    "router": ["routing_failure", "network_drop", "latency_issue"],
    "switch-l3": ["routing_failure", "network_drop"],
    "aaa-server": ["auth_failure"],
    "radius": ["auth_failure"],
    "ldap": ["auth_failure"],
    "vpn-gw": ["auth_failure", "tls_failure"],
    "load-balancer": ["tls_failure", "latency_issue"],
    "proxy": ["tls_failure", "auth_failure"],
    "api-gateway": ["auth_failure", "tls_failure"],
    "idp": ["auth_failure"],
    "kafka": ["event_delivery_failure", "auth_failure"],
    "message-broker": ["event_delivery_failure"],
    "sidecar": ["mesh_failure", "tls_failure"],
    "service-mesh": ["mesh_failure", "tls_failure", "auth_failure"],
}


def _augment_from_graph(
    canonical_hops: list[dict[str, Any]],
    graph: dict[str, Any],
    fault_category: str,
) -> list[dict[str, Any]]:
    """Augment canonical suspect hops with topology-derived nodes.

    Scans the graph for nodes whose type matches fault_category suspects.
    Returns an extended hop list prefixed with topology-specific nodes.
    """
    nodes = graph.get("nodes", [])
    extra_suspects: list[dict[str, Any]] = []
    next_id = ord("Z")  # allocate IDs beyond Z range
    for node in nodes:
        ntype = (node.get("type") or node.get("nodeType") or "").lower()
        fault_types = _TYPE_TO_FAULT.get(ntype, [])
        if fault_category in fault_types:
            label = node.get("label") or node.get("id") or "Node"
            extra_suspects.append(
                {
                    "id": f"T{chr(next_id)}",
                    "label": label,
                    "suspect": True,
                    "source": "topology",
                }
            )
            next_id += 1
            if next_id > ord("Z") + 20:
                break  # Safety cap
    # Prepend topology suspects before canonical path
    return extra_suspects + canonical_hops if extra_suspects else canonical_hops


# ---------------------------------------------------------------------------
# Mermaid sub-diagram generation
# ---------------------------------------------------------------------------


def _generate_mermaid(canvas_type: str, fault_category: str) -> str:
    """Generate a Mermaid LR flowchart with suspect nodes styled red.

    Uses classDef for coloring: 'suspect' (red), 'ok' (green).
    """
    resolved = _CANVAS_ALIAS.get(canvas_type, canvas_type)
    canvas_paths = _HOP_PATHS.get(resolved, _HOP_PATHS["ndc"])
    hops = canvas_paths.get(fault_category) or next(iter(canvas_paths.values()))

    lines = [
        "graph LR",
        "    classDef suspect fill:#ff6b6b,stroke:#c0392b,color:#fff,stroke-width:2px",
        "    classDef ok fill:#e8f5e9,stroke:#388e3c,color:#1b5e20",
    ]

    for hop in hops:
        node_id = hop["id"]
        label = hop["label"]
        cls = "suspect" if hop["suspect"] else "ok"
        lines.append(f'    {node_id}["{label}"]:::{cls}')

    for i in range(len(hops) - 1):
        lines.append(f"    {hops[i]['id']} --> {hops[i + 1]['id']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary text builder
# ---------------------------------------------------------------------------

_CATEGORY_DISPLAY: dict[str, str] = {
    "auth_failure": "authentication failure",
    "tls_failure": "TLS/certificate failure",
    "network_drop": "network packet drop",
    "routing_failure": "routing failure",
    "event_delivery_failure": "event delivery failure",
    "latency_issue": "latency / performance degradation",
    "mesh_failure": "service mesh failure",
}


def _build_summary(
    canvas_name: str,
    fault_category: str,
    root_causes: list[dict[str, Any]],
    suspect_hops: list[dict[str, Any]],
) -> str:
    """Build a human-readable diagnosis narrative."""
    category_label = _CATEGORY_DISPLAY.get(fault_category, fault_category.replace("_", " "))
    suspect_labels = [
        h["label"].split("(")[0].strip()
        for h in suspect_hops
    ]

    lines = [
        f"[TROUBLESHOOT — {canvas_name}]",
        "",
        f"Symptom classified as: {category_label}",
        "",
        f"Suspect hops ({len(suspect_labels)} candidate{'s' if len(suspect_labels) != 1 else ''}):",
    ]
    for i, label in enumerate(suspect_labels[:5], 1):
        lines.append(f"  {i}. {label}")

    lines += [
        "",
        "Root causes ranked by likelihood:",
    ]
    for rc in root_causes[:4]:
        lines.append(f"  [{rc['rank']}] {rc['root_cause']}")
        lines.append(f"      Evidence: {rc['evidence']}")

    lines += [
        "",
        "The sub-diagram below highlights the suspect path in red.",
        "Focus investigation on the marked hops first.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def localize_fault(
    symptom_text: str,
    canvas_type: str,
    graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Localize a fault from free-form symptom text.

    Args:
        symptom_text : User-described symptom (e.g. 'auth failing between services').
        canvas_type  : Canvas context — 'ndc', 'sdc', 'eda', etc.
        graph        : Optional topology dict {nodes, edges} for topology-aware traversal.

    Returns dict with keys:
        fault_category, all_categories, suspect_hops, root_causes,
        sub_diagram_mermaid, summary_text.
    """
    categories = classify_symptom(symptom_text)
    primary = categories[0]

    # Resolve canvas to a profiled key
    resolved = _CANVAS_ALIAS.get(canvas_type, canvas_type)
    canvas_profiles = _FAULT_PROFILES.get(resolved, _FAULT_PROFILES["ndc"])
    root_causes = canvas_profiles.get(primary) or next(iter(canvas_profiles.values()))

    # Get canonical hop path for this fault
    canvas_paths = _HOP_PATHS.get(resolved, _HOP_PATHS["ndc"])
    hop_path = canvas_paths.get(primary) or next(iter(canvas_paths.values()))

    # Optionally augment with topology nodes
    if graph and graph.get("nodes"):
        hop_path = _augment_from_graph(hop_path, graph, primary)

    suspect_hops = [h for h in hop_path if h["suspect"]]

    sub_diagram = _generate_mermaid(canvas_type, primary)
    canvas_name = _CANVAS_DISPLAY.get(canvas_type, canvas_type.upper())
    summary = _build_summary(canvas_name, primary, root_causes, suspect_hops)

    return {
        "fault_category": primary,
        "all_categories": categories,
        "suspect_hops": [
            {
                "rank": i + 1,
                "node": h["label"].split("(")[0].strip(),
                "id": h["id"],
            }
            for i, h in enumerate(suspect_hops)
        ],
        "root_causes": root_causes,
        "sub_diagram_mermaid": sub_diagram,
        "summary_text": summary,
    }
