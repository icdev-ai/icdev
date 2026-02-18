#!/usr/bin/env python3
"""Generate Kubernetes manifests with CUI metadata labels.
Produces Deployment, Service, Ingress, ConfigMap, NetworkPolicy, and HPA."""

import argparse
import json
import yaml as _yaml_mod
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

CUI_HEADER = (
    "# //CUI\n"
    "# CONTROLLED UNCLASSIFIED INFORMATION\n"
    "# Authorized for: Internal project use only\n"
    "# Generated: {timestamp}\n"
    "# Generator: ICDev K8s Generator\n"
    "# //CUI\n"
)

CUI_LABELS = {
    "classification": "CUI",
    "managed-by": "icdev",
}


def _cui_header() -> str:
    return CUI_HEADER.format(timestamp=datetime.utcnow().isoformat())


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _labels(app_name: str, extra: dict = None) -> dict:
    base = {
        "app.kubernetes.io/name": app_name,
        "app.kubernetes.io/managed-by": "icdev",
        **CUI_LABELS,
    }
    if extra:
        base.update(extra)
    return base


def _yaml_dump(obj: dict) -> str:
    """Dump dict to YAML. Uses PyYAML if available, else manual formatting."""
    try:
        return _yaml_mod.dump(obj, default_flow_style=False, sort_keys=False)
    except Exception:
        return json.dumps(obj, indent=2)


try:
    import yaml as _yaml_mod
except ImportError:
    _yaml_mod = None

    def _yaml_dump(obj: dict) -> str:
        return json.dumps(obj, indent=2)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------
def generate_deployment(project_path: str, app_config: dict = None) -> list:
    """Generate Deployment with resource limits, health checks, security context."""
    config = app_config or {}
    name = config.get("name", "icdev-app")
    image = config.get("image", "registry.example.com/app:latest")
    port = config.get("port", 8080)
    replicas = config.get("replicas", 3)
    namespace = config.get("namespace", "default")
    cpu_request = config.get("cpu_request", "100m")
    cpu_limit = config.get("cpu_limit", "500m")
    mem_request = config.get("mem_request", "128Mi")
    mem_limit = config.get("mem_limit", "512Mi")

    k8s_dir = Path(project_path) / "k8s"

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(name, {"app.kubernetes.io/component": "server"}),
            "annotations": {
                "classification": "CUI",
                "icdev.io/generated": datetime.utcnow().isoformat(),
            },
        },
        "spec": {
            "replicas": replicas,
            "selector": {
                "matchLabels": {"app.kubernetes.io/name": name},
            },
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {
                    "maxUnavailable": 1,
                    "maxSurge": 1,
                },
            },
            "template": {
                "metadata": {
                    "labels": _labels(name),
                    "annotations": {
                        "classification": "CUI",
                        "prometheus.io/scrape": "true",
                        "prometheus.io/port": str(port),
                        "prometheus.io/path": "/metrics",
                    },
                },
                "spec": {
                    "serviceAccountName": name,
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 65534,
                        "runAsGroup": 65534,
                        "fsGroup": 65534,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": name,
                            "image": image,
                            "imagePullPolicy": "Always",
                            "ports": [
                                {
                                    "name": "http",
                                    "containerPort": port,
                                    "protocol": "TCP",
                                }
                            ],
                            "resources": {
                                "requests": {"cpu": cpu_request, "memory": mem_request},
                                "limits": {"cpu": cpu_limit, "memory": mem_limit},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "/health/live", "port": port},
                                "initialDelaySeconds": 15,
                                "periodSeconds": 20,
                                "timeoutSeconds": 5,
                                "failureThreshold": 3,
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "/health/ready", "port": port},
                                "initialDelaySeconds": 5,
                                "periodSeconds": 10,
                                "timeoutSeconds": 3,
                                "failureThreshold": 3,
                            },
                            "startupProbe": {
                                "httpGet": {"path": "/health/live", "port": port},
                                "failureThreshold": 30,
                                "periodSeconds": 10,
                            },
                            "volumeMounts": [
                                {"name": "tmp", "mountPath": "/tmp"},
                                {"name": "config", "mountPath": "/etc/app/config", "readOnly": True},
                            ],
                            "env": [
                                {"name": "APP_PORT", "value": str(port)},
                                {"name": "LOG_LEVEL", "value": "info"},
                                {"name": "CLASSIFICATION", "value": "CUI"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "tmp", "emptyDir": {"sizeLimit": "100Mi"}},
                        {"name": "config", "configMap": {"name": f"{name}-config"}},
                    ],
                    "topologySpreadConstraints": [
                        {
                            "maxSkew": 1,
                            "topologyKey": "kubernetes.io/hostname",
                            "whenUnsatisfiable": "DoNotSchedule",
                            "labelSelector": {
                                "matchLabels": {"app.kubernetes.io/name": name},
                            },
                        }
                    ],
                },
            },
        },
    }

    content = _cui_header() + _yaml_dump(deployment)
    p = _write(k8s_dir / "deployment.yaml", content)

    # Also generate ServiceAccount
    sa = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(name),
        },
        "automountServiceAccountToken": False,
    }
    sa_content = _cui_header() + _yaml_dump(sa)
    p2 = _write(k8s_dir / "serviceaccount.yaml", sa_content)

    return [str(p), str(p2)]


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
def generate_service(project_path: str, app_config: dict = None) -> list:
    """Generate ClusterIP Service."""
    config = app_config or {}
    name = config.get("name", "icdev-app")
    port = config.get("port", 8080)
    namespace = config.get("namespace", "default")

    k8s_dir = Path(project_path) / "k8s"

    svc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(name),
            "annotations": {"classification": "CUI"},
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {"app.kubernetes.io/name": name},
            "ports": [
                {
                    "name": "http",
                    "port": port,
                    "targetPort": "http",
                    "protocol": "TCP",
                }
            ],
        },
    }

    content = _cui_header() + _yaml_dump(svc)
    p = _write(k8s_dir / "service.yaml", content)
    return [str(p)]


# ---------------------------------------------------------------------------
# Ingress
# ---------------------------------------------------------------------------
def generate_ingress(project_path: str, app_config: dict = None) -> list:
    """Generate Ingress with TLS."""
    config = app_config or {}
    name = config.get("name", "icdev-app")
    port = config.get("port", 8080)
    namespace = config.get("namespace", "default")
    hostname = config.get("hostname", f"{name}.internal.example.com")
    tls_secret = config.get("tls_secret", f"{name}-tls")

    k8s_dir = Path(project_path) / "k8s"

    ingress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": _labels(name),
            "annotations": {
                "classification": "CUI",
                "nginx.ingress.kubernetes.io/ssl-redirect": "true",
                "nginx.ingress.kubernetes.io/force-ssl-redirect": "true",
                "nginx.ingress.kubernetes.io/proxy-body-size": "10m",
                "nginx.ingress.kubernetes.io/rate-limit": "100",
                "nginx.ingress.kubernetes.io/rate-limit-window": "1m",
                "nginx.ingress.kubernetes.io/configuration-snippet": (
                    'more_set_headers "X-Frame-Options: DENY";\n'
                    'more_set_headers "X-Content-Type-Options: nosniff";\n'
                    'more_set_headers "X-XSS-Protection: 1; mode=block";\n'
                    'more_set_headers "Strict-Transport-Security: max-age=31536000; includeSubDomains";'
                ),
            },
        },
        "spec": {
            "ingressClassName": "nginx",
            "tls": [
                {
                    "hosts": [hostname],
                    "secretName": tls_secret,
                }
            ],
            "rules": [
                {
                    "host": hostname,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": name,
                                        "port": {"number": port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }

    content = _cui_header() + _yaml_dump(ingress)
    p = _write(k8s_dir / "ingress.yaml", content)
    return [str(p)]


# ---------------------------------------------------------------------------
# ConfigMap
# ---------------------------------------------------------------------------
def generate_configmap(project_path: str, config: dict = None) -> list:
    """Generate ConfigMap."""
    config = config or {}
    name = config.get("name", "icdev-app")
    namespace = config.get("namespace", "default")
    data = config.get("data", {
        "LOG_LEVEL": "info",
        "APP_ENV": "production",
        "CLASSIFICATION": "CUI",
        "METRICS_ENABLED": "true",
        "HEALTH_CHECK_PATH": "/health",
    })

    k8s_dir = Path(project_path) / "k8s"

    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": f"{name}-config",
            "namespace": namespace,
            "labels": _labels(name),
            "annotations": {"classification": "CUI"},
        },
        "data": data,
    }

    content = _cui_header() + _yaml_dump(cm)
    p = _write(k8s_dir / "configmap.yaml", content)
    return [str(p)]


# ---------------------------------------------------------------------------
# NetworkPolicy
# ---------------------------------------------------------------------------
def generate_networkpolicy(project_path: str, app_config: dict = None) -> list:
    """Generate NetworkPolicy restricting ingress/egress."""
    config = app_config or {}
    name = config.get("name", "icdev-app")
    port = config.get("port", 8080)
    namespace = config.get("namespace", "default")

    k8s_dir = Path(project_path) / "k8s"

    netpol = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"{name}-netpol",
            "namespace": namespace,
            "labels": _labels(name),
            "annotations": {"classification": "CUI"},
        },
        "spec": {
            "podSelector": {
                "matchLabels": {"app.kubernetes.io/name": name},
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"},
                            }
                        },
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": "monitoring"},
                            }
                        },
                    ],
                    "ports": [
                        {"protocol": "TCP", "port": port},
                    ],
                }
            ],
            "egress": [
                {
                    "to": [
                        {
                            "namespaceSelector": {},
                            "podSelector": {
                                "matchLabels": {"app.kubernetes.io/name": "postgres"},
                            },
                        }
                    ],
                    "ports": [
                        {"protocol": "TCP", "port": 5432},
                    ],
                },
                {
                    "to": [],
                    "ports": [
                        {"protocol": "UDP", "port": 53},
                        {"protocol": "TCP", "port": 53},
                    ],
                },
            ],
        },
    }

    content = _cui_header() + _yaml_dump(netpol)
    p = _write(k8s_dir / "networkpolicy.yaml", content)
    return [str(p)]


# ---------------------------------------------------------------------------
# HPA
# ---------------------------------------------------------------------------
def generate_hpa(project_path: str, config: dict = None) -> list:
    """Generate HorizontalPodAutoscaler."""
    config = config or {}
    name = config.get("name", "icdev-app")
    namespace = config.get("namespace", "default")
    min_replicas = config.get("min_replicas", 2)
    max_replicas = config.get("max_replicas", 10)
    cpu_target = config.get("cpu_target_percent", 70)
    mem_target = config.get("memory_target_percent", 80)

    k8s_dir = Path(project_path) / "k8s"

    hpa = {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": f"{name}-hpa",
            "namespace": namespace,
            "labels": _labels(name),
            "annotations": {"classification": "CUI"},
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": name,
            },
            "minReplicas": min_replicas,
            "maxReplicas": max_replicas,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": cpu_target,
                        },
                    },
                },
                {
                    "type": "Resource",
                    "resource": {
                        "name": "memory",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": mem_target,
                        },
                    },
                },
            ],
            "behavior": {
                "scaleDown": {
                    "stabilizationWindowSeconds": 300,
                    "policies": [
                        {"type": "Percent", "value": 25, "periodSeconds": 60},
                    ],
                },
                "scaleUp": {
                    "stabilizationWindowSeconds": 60,
                    "policies": [
                        {"type": "Percent", "value": 50, "periodSeconds": 60},
                        {"type": "Pods", "value": 4, "periodSeconds": 60},
                    ],
                    "selectPolicy": "Max",
                },
            },
        },
    }

    content = _cui_header() + _yaml_dump(hpa)
    p = _write(k8s_dir / "hpa.yaml", content)
    return [str(p)]


# ---------------------------------------------------------------------------
# Agent Deployments (Phase 19 — per-agent K8s manifests)
# ---------------------------------------------------------------------------
# Default agent definitions matching ICDEV multi-agent architecture
DEFAULT_AGENTS = [
    {"name": "orchestrator", "port": 8443, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
    {"name": "architect", "port": 8444, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
    {"name": "builder", "port": 8445, "cpu_request": "500m", "cpu_limit": "2000m", "mem_request": "512Mi", "mem_limit": "2Gi"},
    {"name": "compliance", "port": 8446, "cpu_request": "100m", "cpu_limit": "500m", "mem_request": "128Mi", "mem_limit": "512Mi"},
    {"name": "security", "port": 8447, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
    {"name": "infrastructure", "port": 8448, "cpu_request": "100m", "cpu_limit": "500m", "mem_request": "128Mi", "mem_limit": "512Mi"},
    {"name": "knowledge", "port": 8449, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
    {"name": "monitor", "port": 8450, "cpu_request": "100m", "cpu_limit": "500m", "mem_request": "128Mi", "mem_limit": "512Mi"},
    {"name": "mbse", "port": 8451, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
    {"name": "modernization", "port": 8452, "cpu_request": "200m", "cpu_limit": "1000m", "mem_request": "256Mi", "mem_limit": "1Gi"},
]


def generate_agent_deployments(project_path: str, blueprint: dict = None) -> list:
    """Generate K8s deployments for each agent (non-root, read-only rootfs).

    Creates a Deployment and Service for each agent in the ICDEV multi-agent
    architecture. All containers run as non-root with read-only root filesystem,
    drop ALL capabilities, and enforce security context constraints per STIG.

    Args:
        project_path: Target project directory.
        blueprint: Optional dict with 'agents' list and 'namespace' override.

    Returns:
        List of generated file paths.
    """
    config = blueprint or {}
    agents = config.get("agents", DEFAULT_AGENTS)
    namespace = config.get("namespace", "icdev-agents")
    registry = config.get("registry", "registry.example.com/icdev")

    k8s_dir = Path(project_path) / "k8s" / "agents"
    files = []

    for agent in agents:
        agent_name = f"icdev-{agent['name']}"
        port = agent["port"]
        image = f"{registry}/{agent['name']}-agent:latest"

        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": agent_name,
                "namespace": namespace,
                "labels": _labels(agent_name, {
                    "app.kubernetes.io/component": agent["name"],
                    "icdev.io/tier": "agent",
                }),
                "annotations": {
                    "classification": "CUI",
                    "icdev.io/generated": datetime.utcnow().isoformat(),
                    "icdev.io/agent-port": str(port),
                },
            },
            "spec": {
                "replicas": 2,
                "selector": {
                    "matchLabels": {"app.kubernetes.io/name": agent_name},
                },
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
                "template": {
                    "metadata": {
                        "labels": _labels(agent_name, {
                            "app.kubernetes.io/component": agent["name"],
                            "icdev.io/tier": "agent",
                        }),
                        "annotations": {
                            "classification": "CUI",
                            "prometheus.io/scrape": "true",
                            "prometheus.io/port": str(port),
                        },
                    },
                    "spec": {
                        "serviceAccountName": agent_name,
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 1000,
                            "runAsGroup": 1000,
                            "fsGroup": 1000,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": agent["name"],
                                "image": image,
                                "imagePullPolicy": "Always",
                                "ports": [
                                    {"name": "https", "containerPort": port, "protocol": "TCP"},
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": agent.get("cpu_request", "100m"),
                                        "memory": agent.get("mem_request", "128Mi"),
                                    },
                                    "limits": {
                                        "cpu": agent.get("cpu_limit", "500m"),
                                        "memory": agent.get("mem_limit", "512Mi"),
                                    },
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "runAsNonRoot": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "livenessProbe": {
                                    "httpGet": {"path": "/health", "port": port, "scheme": "HTTPS"},
                                    "initialDelaySeconds": 15,
                                    "periodSeconds": 20,
                                    "timeoutSeconds": 5,
                                },
                                "readinessProbe": {
                                    "httpGet": {"path": "/health", "port": port, "scheme": "HTTPS"},
                                    "initialDelaySeconds": 5,
                                    "periodSeconds": 10,
                                    "timeoutSeconds": 3,
                                },
                                "env": [
                                    {"name": "AGENT_NAME", "value": agent["name"]},
                                    {"name": "AGENT_PORT", "value": str(port)},
                                    {"name": "CLASSIFICATION", "value": "CUI"},
                                    {"name": "TLS_CERT_PATH", "value": "/etc/tls/tls.crt"},
                                    {"name": "TLS_KEY_PATH", "value": "/etc/tls/tls.key"},
                                    {"name": "TLS_CA_PATH", "value": "/etc/tls/ca.crt"},
                                ],
                                "volumeMounts": [
                                    {"name": "tmp", "mountPath": "/tmp"},
                                    {"name": "tls-certs", "mountPath": "/etc/tls", "readOnly": True},
                                    {"name": "config", "mountPath": "/etc/agent/config", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "tmp", "emptyDir": {"sizeLimit": "100Mi"}},
                            {"name": "tls-certs", "secret": {"secretName": f"{agent_name}-tls"}},
                            {"name": "config", "configMap": {"name": f"{agent_name}-config"}},
                        ],
                    },
                },
            },
        }

        # Service for agent
        service = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": agent_name,
                "namespace": namespace,
                "labels": _labels(agent_name),
                "annotations": {"classification": "CUI"},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app.kubernetes.io/name": agent_name},
                "ports": [
                    {"name": "https", "port": port, "targetPort": "https", "protocol": "TCP"},
                ],
            },
        }

        # ServiceAccount
        sa = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": agent_name,
                "namespace": namespace,
                "labels": _labels(agent_name),
            },
            "automountServiceAccountToken": False,
        }

        # Write deployment
        dep_content = _cui_header() + _yaml_dump(deployment)
        dep_path = _write(k8s_dir / f"{agent['name']}-deployment.yaml", dep_content)
        files.append(str(dep_path))

        # Write service
        svc_content = _cui_header() + _yaml_dump(service)
        svc_path = _write(k8s_dir / f"{agent['name']}-service.yaml", svc_content)
        files.append(str(svc_path))

        # Write service account
        sa_content = _cui_header() + _yaml_dump(sa)
        sa_path = _write(k8s_dir / f"{agent['name']}-sa.yaml", sa_content)
        files.append(str(sa_path))

    return files


def generate_predictive_hpa(project_path: str, blueprint: dict = None) -> list:
    """Generate HPA configs for agent auto-scaling.

    Creates HorizontalPodAutoscaler resources for each agent with
    predictive scaling behavior. Core agents (orchestrator, builder)
    get more aggressive scaling; support agents scale conservatively.

    Args:
        project_path: Target project directory.
        blueprint: Optional dict with 'agents' list and 'namespace' override.

    Returns:
        List of generated file paths.
    """
    config = blueprint or {}
    agents = config.get("agents", DEFAULT_AGENTS)
    namespace = config.get("namespace", "icdev-agents")

    # Tier-based scaling profiles
    core_agents = {"orchestrator", "architect"}
    domain_agents = {"builder", "compliance", "security", "infrastructure", "mbse", "modernization"}
    # support agents = everything else (knowledge, monitor)

    k8s_dir = Path(project_path) / "k8s" / "agents"
    files = []

    for agent in agents:
        agent_name = f"icdev-{agent['name']}"
        name_key = agent["name"]

        # Determine scaling profile by tier
        if name_key in core_agents:
            min_replicas = 2
            max_replicas = 6
            cpu_target = 60
            mem_target = 70
            scale_up_percent = 100
            scale_down_window = 300
        elif name_key in domain_agents:
            min_replicas = 1
            max_replicas = 8
            cpu_target = 70
            mem_target = 80
            scale_up_percent = 50
            scale_down_window = 300
        else:
            # Support agents — conservative scaling
            min_replicas = 1
            max_replicas = 4
            cpu_target = 75
            mem_target = 85
            scale_up_percent = 25
            scale_down_window = 600

        hpa = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {
                "name": f"{agent_name}-hpa",
                "namespace": namespace,
                "labels": _labels(agent_name, {
                    "icdev.io/scaling-tier": "core" if name_key in core_agents
                    else "domain" if name_key in domain_agents
                    else "support",
                }),
                "annotations": {
                    "classification": "CUI",
                    "icdev.io/scaling-profile": "predictive",
                },
            },
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": agent_name,
                },
                "minReplicas": min_replicas,
                "maxReplicas": max_replicas,
                "metrics": [
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "cpu",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": cpu_target,
                            },
                        },
                    },
                    {
                        "type": "Resource",
                        "resource": {
                            "name": "memory",
                            "target": {
                                "type": "Utilization",
                                "averageUtilization": mem_target,
                            },
                        },
                    },
                ],
                "behavior": {
                    "scaleDown": {
                        "stabilizationWindowSeconds": scale_down_window,
                        "policies": [
                            {"type": "Percent", "value": 25, "periodSeconds": 60},
                        ],
                    },
                    "scaleUp": {
                        "stabilizationWindowSeconds": 30,
                        "policies": [
                            {"type": "Percent", "value": scale_up_percent, "periodSeconds": 60},
                            {"type": "Pods", "value": 2, "periodSeconds": 60},
                        ],
                        "selectPolicy": "Max",
                    },
                },
            },
        }

        content = _cui_header() + _yaml_dump(hpa)
        p = _write(k8s_dir / f"{agent['name']}-hpa.yaml", content)
        files.append(str(p))

    return files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Generate Kubernetes manifests")
    parser.add_argument("--project-path", required=True, help="Target project directory")
    parser.add_argument("--name", default="icdev-app", help="Application name")
    parser.add_argument("--image", default="registry.example.com/app:latest", help="Container image")
    parser.add_argument("--port", type=int, default=8080, help="Application port")
    parser.add_argument("--namespace", default="default", help="Kubernetes namespace")
    parser.add_argument("--replicas", type=int, default=3, help="Number of replicas")
    parser.add_argument("--hostname", default=None, help="Ingress hostname")
    parser.add_argument(
        "--manifests",
        default="deployment,service,ingress,configmap,networkpolicy,hpa",
        help="Comma-separated manifests to generate (also: agent-deployments, agent-hpa)",
    )
    args = parser.parse_args()

    app_config = {
        "name": args.name,
        "image": args.image,
        "port": args.port,
        "namespace": args.namespace,
        "replicas": args.replicas,
        "hostname": args.hostname or f"{args.name}.internal.example.com",
    }

    manifests = [m.strip() for m in args.manifests.split(",")]
    all_files = []

    generators = {
        "deployment": lambda: generate_deployment(args.project_path, app_config),
        "service": lambda: generate_service(args.project_path, app_config),
        "ingress": lambda: generate_ingress(args.project_path, app_config),
        "configmap": lambda: generate_configmap(args.project_path, app_config),
        "networkpolicy": lambda: generate_networkpolicy(args.project_path, app_config),
        "hpa": lambda: generate_hpa(args.project_path, app_config),
        "agent-deployments": lambda: generate_agent_deployments(args.project_path, app_config),
        "agent-hpa": lambda: generate_predictive_hpa(args.project_path, app_config),
    }

    for m in manifests:
        if m in generators:
            files = generators[m]()
            all_files.extend(files)
            print(f"[k8s] Generated {m}: {len(files)} files")
        else:
            print(f"[k8s] Unknown manifest: {m}")

    print(f"\n[k8s] Total files generated: {len(all_files)}")
    for f in all_files:
        print(f"  -> {f}")


if __name__ == "__main__":
    main()
