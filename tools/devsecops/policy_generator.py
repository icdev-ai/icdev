#!/usr/bin/env python3
# CUI // SP-CTI
"""Policy-as-Code Generator — Kyverno and OPA/Gatekeeper policy generation.

Generates admission controller policies based on project DevSecOps profile.
Supports both Kyverno (K8s-native YAML) and OPA/Gatekeeper (Rego-based).

ADR D121: Both Kyverno and OPA supported; customer selects in profile.

Usage:
    python tools/devsecops/policy_generator.py --project-id "proj-123" --engine kyverno --json
    python tools/devsecops/policy_generator.py --project-id "proj-123" --engine opa --json
    python tools/devsecops/policy_generator.py --project-id "proj-123" --engine kyverno --output /tmp/policies/
    python tools/devsecops/policy_generator.py --project-id "proj-123" --admission-config --json
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml

    def _to_yaml(data: dict) -> str:
        return yaml.dump(data, default_flow_style=False, sort_keys=False)
except ImportError:
    yaml = None

    def _to_yaml(data: dict) -> str:
        return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = BASE_DIR / "args" / "devsecops_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_profile(project_id: str) -> dict:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        if not row:
            return {}
        return {
            "maturity_level": row["maturity_level"],
            "active_stages": json.loads(row["active_stages"] or "[]"),
            "stage_configs": json.loads(row["stage_configs"] or "{}"),
        }
    finally:
        conn.close()


def _get_project_info(project_id: str) -> dict:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT name, classification, impact_level FROM projects WHERE id = ?",
            (project_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {"name": "unknown", "classification": "CUI", "impact_level": "IL4"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Kyverno policies
# ---------------------------------------------------------------------------

def _kyverno_pod_security() -> dict:
    """Kyverno policy: enforce pod security standards."""
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "devsecops-pod-security",
            "annotations": {
                "policies.kyverno.io/title": "Pod Security Standards",
                "policies.kyverno.io/category": "DevSecOps",
                "policies.kyverno.io/severity": "high",
                "policies.kyverno.io/description": "Enforce pod security: non-root, read-only rootfs, drop capabilities",
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": True,
            "rules": [
                {
                    "name": "run-as-non-root",
                    "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                    "validate": {
                        "message": "Pods must run as non-root",
                        "pattern": {
                            "spec": {
                                "securityContext": {"runAsNonRoot": True},
                                "containers": [{"securityContext": {"runAsNonRoot": True}}],
                            }
                        },
                    },
                },
                {
                    "name": "read-only-rootfs",
                    "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                    "validate": {
                        "message": "Containers must use read-only root filesystem",
                        "pattern": {
                            "spec": {
                                "containers": [{"securityContext": {"readOnlyRootFilesystem": True}}]
                            }
                        },
                    },
                },
                {
                    "name": "drop-all-capabilities",
                    "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                    "validate": {
                        "message": "Containers must drop ALL capabilities",
                        "pattern": {
                            "spec": {
                                "containers": [{
                                    "securityContext": {
                                        "capabilities": {"drop": ["ALL"]}
                                    }
                                }]
                            }
                        },
                    },
                },
            ],
        },
    }


def _kyverno_image_registry(allowed_registries: list = None) -> dict:
    """Kyverno policy: restrict image registries."""
    registries = allowed_registries or [
        "*.dkr.ecr.us-gov-west-1.amazonaws.com/*",
        "registry.il*.dso.mil/*",
    ]
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "devsecops-image-registry",
            "annotations": {
                "policies.kyverno.io/title": "Restrict Image Registries",
                "policies.kyverno.io/category": "DevSecOps",
                "policies.kyverno.io/severity": "high",
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": True,
            "rules": [{
                "name": "validate-image-registry",
                "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                "validate": {
                    "message": "Images must come from approved registries",
                    "pattern": {
                        "spec": {
                            "containers": [{"image": f"{r}"} for r in registries]
                        }
                    },
                },
            }],
        },
    }


def _kyverno_require_labels(project_name: str, classification: str) -> dict:
    """Kyverno policy: require classification and management labels."""
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "devsecops-require-labels",
            "annotations": {
                "policies.kyverno.io/title": "Required Labels",
                "policies.kyverno.io/category": "DevSecOps",
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": True,
            "rules": [{
                "name": "require-classification-label",
                "match": {"any": [{"resources": {"kinds": ["Pod", "Deployment", "StatefulSet"]}}]},
                "validate": {
                    "message": "Resources must have classification and managed-by labels",
                    "pattern": {
                        "metadata": {
                            "labels": {
                                "app.kubernetes.io/managed-by": "?*",
                                "icdev.mil/classification": f"{classification}",
                            }
                        }
                    },
                },
            }],
        },
    }


def _kyverno_network_policy_required() -> dict:
    """Kyverno policy: require NetworkPolicy per namespace."""
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "devsecops-require-network-policy",
            "annotations": {
                "policies.kyverno.io/title": "Require Network Policy",
                "policies.kyverno.io/category": "DevSecOps/ZTA",
            },
        },
        "spec": {
            "validationFailureAction": "Audit",
            "background": True,
            "rules": [{
                "name": "require-network-policy",
                "match": {"any": [{"resources": {"kinds": ["Namespace"]}}]},
                "validate": {
                    "message": "Each namespace must have at least one NetworkPolicy (ZTA requirement)",
                    "deny": {
                        "conditions": {
                            "any": [{
                                "key": "{{request.object.metadata.labels.\"icdev.mil/network-policy\"}}",
                                "operator": "Equals",
                                "value": "",
                            }]
                        }
                    },
                },
            }],
        },
    }


def _kyverno_resource_limits() -> dict:
    """Kyverno policy: enforce resource limits."""
    return {
        "apiVersion": "kyverno.io/v1",
        "kind": "ClusterPolicy",
        "metadata": {
            "name": "devsecops-resource-limits",
            "annotations": {
                "policies.kyverno.io/title": "Require Resource Limits",
                "policies.kyverno.io/category": "DevSecOps",
            },
        },
        "spec": {
            "validationFailureAction": "Enforce",
            "background": True,
            "rules": [{
                "name": "require-limits",
                "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                "validate": {
                    "message": "Containers must have CPU and memory limits set",
                    "pattern": {
                        "spec": {
                            "containers": [{
                                "resources": {
                                    "limits": {
                                        "cpu": "?*",
                                        "memory": "?*",
                                    }
                                }
                            }]
                        }
                    },
                },
            }],
        },
    }


def generate_kyverno_policies(project_id: str, profile: dict = None) -> dict:
    """Generate Kyverno policies for a project.

    Returns:
        Dict with policies list, yaml_content, policy_count.
    """
    if profile is None:
        profile = _get_profile(project_id)
    project = _get_project_info(project_id)

    policies = [
        _kyverno_pod_security(),
        _kyverno_image_registry(),
        _kyverno_require_labels(
            project.get("name", "unknown"),
            project.get("classification", "CUI"),
        ),
        _kyverno_network_policy_required(),
        _kyverno_resource_limits(),
    ]

    yaml_docs = []
    for p in policies:
        yaml_docs.append(f"# CUI // SP-CTI\n---\n{_to_yaml(p)}")

    return {
        "project_id": project_id,
        "engine": "kyverno",
        "policy_count": len(policies),
        "policies": [p["metadata"]["name"] for p in policies],
        "yaml_content": "\n".join(yaml_docs),
    }


# ---------------------------------------------------------------------------
# OPA/Gatekeeper policies
# ---------------------------------------------------------------------------

def _opa_pod_security_template() -> dict:
    """OPA ConstraintTemplate for pod security."""
    return {
        "apiVersion": "templates.gatekeeper.sh/v1",
        "kind": "ConstraintTemplate",
        "metadata": {"name": "devsecopspodpolicy"},
        "spec": {
            "crd": {
                "spec": {
                    "names": {"kind": "DevSecOpsPodPolicy"},
                }
            },
            "targets": [{
                "target": "admission.k8s.gatekeeper.sh",
                "rego": """
package devsecopspodpolicy

# Deny pods running as root
violation[{"msg": msg}] {
    input.review.object.spec.containers[_].securityContext.runAsNonRoot != true
    msg := "Container must run as non-root (DevSecOps policy)"
}

# Deny pods without read-only rootfs
violation[{"msg": msg}] {
    input.review.object.spec.containers[_].securityContext.readOnlyRootFilesystem != true
    msg := "Container must use read-only root filesystem (DevSecOps policy)"
}

# Deny pods that don't drop ALL capabilities
violation[{"msg": msg}] {
    caps := input.review.object.spec.containers[_].securityContext.capabilities.drop
    not array_contains(caps, "ALL")
    msg := "Container must drop ALL capabilities (DevSecOps policy)"
}

array_contains(arr, elem) {
    arr[_] == elem
}
""",
            }],
        },
    }


def _opa_pod_security_constraint() -> dict:
    """OPA Constraint applying pod security template."""
    return {
        "apiVersion": "constraints.gatekeeper.sh/v1beta1",
        "kind": "DevSecOpsPodPolicy",
        "metadata": {"name": "devsecops-pod-security"},
        "spec": {
            "match": {
                "kinds": [{"apiGroups": [""], "kinds": ["Pod"]}],
            },
        },
    }


def _opa_image_registry_template() -> dict:
    """OPA ConstraintTemplate for image registry restriction."""
    return {
        "apiVersion": "templates.gatekeeper.sh/v1",
        "kind": "ConstraintTemplate",
        "metadata": {"name": "devsecopsimageregistry"},
        "spec": {
            "crd": {
                "spec": {
                    "names": {"kind": "DevSecOpsImageRegistry"},
                    "validation": {
                        "openAPIV3Schema": {
                            "type": "object",
                            "properties": {
                                "allowedRegistries": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            },
            "targets": [{
                "target": "admission.k8s.gatekeeper.sh",
                "rego": """
package devsecopsimageregistry

violation[{"msg": msg}] {
    container := input.review.object.spec.containers[_]
    not registry_allowed(container.image)
    msg := sprintf("Image '%v' is not from an approved registry (DevSecOps policy)", [container.image])
}

registry_allowed(image) {
    allowed := input.parameters.allowedRegistries[_]
    startswith(image, allowed)
}
""",
            }],
        },
    }


def _opa_image_registry_constraint() -> dict:
    """OPA Constraint for image registry."""
    return {
        "apiVersion": "constraints.gatekeeper.sh/v1beta1",
        "kind": "DevSecOpsImageRegistry",
        "metadata": {"name": "devsecops-image-registry"},
        "spec": {
            "match": {
                "kinds": [{"apiGroups": [""], "kinds": ["Pod"]}],
            },
            "parameters": {
                "allowedRegistries": [
                    "*.dkr.ecr.us-gov-west-1.amazonaws.com/",
                    "registry.il*.dso.mil/",
                ],
            },
        },
    }


def _opa_resource_limits_template() -> dict:
    """OPA ConstraintTemplate for resource limits."""
    return {
        "apiVersion": "templates.gatekeeper.sh/v1",
        "kind": "ConstraintTemplate",
        "metadata": {"name": "devsecopsresourcelimits"},
        "spec": {
            "crd": {
                "spec": {
                    "names": {"kind": "DevSecOpsResourceLimits"},
                }
            },
            "targets": [{
                "target": "admission.k8s.gatekeeper.sh",
                "rego": """
package devsecopsresourcelimits

violation[{"msg": msg}] {
    container := input.review.object.spec.containers[_]
    not container.resources.limits.cpu
    msg := sprintf("Container '%v' must have CPU limits (DevSecOps policy)", [container.name])
}

violation[{"msg": msg}] {
    container := input.review.object.spec.containers[_]
    not container.resources.limits.memory
    msg := sprintf("Container '%v' must have memory limits (DevSecOps policy)", [container.name])
}
""",
            }],
        },
    }


def _opa_resource_limits_constraint() -> dict:
    """OPA Constraint for resource limits."""
    return {
        "apiVersion": "constraints.gatekeeper.sh/v1beta1",
        "kind": "DevSecOpsResourceLimits",
        "metadata": {"name": "devsecops-resource-limits"},
        "spec": {
            "match": {
                "kinds": [{"apiGroups": [""], "kinds": ["Pod"]}],
            },
        },
    }


def generate_opa_policies(project_id: str, profile: dict = None) -> dict:
    """Generate OPA/Gatekeeper policies for a project.

    Returns:
        Dict with templates, constraints, yaml_content, policy_count.
    """
    if profile is None:
        profile = _get_profile(project_id)

    templates = [
        _opa_pod_security_template(),
        _opa_image_registry_template(),
        _opa_resource_limits_template(),
    ]
    constraints = [
        _opa_pod_security_constraint(),
        _opa_image_registry_constraint(),
        _opa_resource_limits_constraint(),
    ]

    yaml_docs = []
    for t in templates:
        yaml_docs.append(f"# CUI // SP-CTI\n---\n{_to_yaml(t)}")
    for c in constraints:
        yaml_docs.append(f"# CUI // SP-CTI\n---\n{_to_yaml(c)}")

    return {
        "project_id": project_id,
        "engine": "opa_gatekeeper",
        "template_count": len(templates),
        "constraint_count": len(constraints),
        "templates": [t["metadata"]["name"] for t in templates],
        "constraints": [c["metadata"]["name"] for c in constraints],
        "yaml_content": "\n".join(yaml_docs),
    }


# ---------------------------------------------------------------------------
# Admission controller config
# ---------------------------------------------------------------------------

def generate_admission_config(project_id: str, engine: str = "kyverno") -> dict:
    """Generate admission controller installation config.

    Returns:
        Dict with install instructions, namespace config, YAML.
    """
    if engine == "kyverno":
        config = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "kyverno",
                "labels": {
                    "app.kubernetes.io/managed-by": "icdev",
                    "icdev.mil/component": "devsecops-policy-engine",
                },
            },
        }
        return {
            "project_id": project_id,
            "engine": "kyverno",
            "install_command": "helm install kyverno kyverno/kyverno -n kyverno --create-namespace",
            "namespace_yaml": _to_yaml(config),
            "helm_repo": "https://kyverno.github.io/kyverno/",
        }
    else:
        config = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "gatekeeper-system",
                "labels": {
                    "app.kubernetes.io/managed-by": "icdev",
                    "icdev.mil/component": "devsecops-policy-engine",
                },
            },
        }
        return {
            "project_id": project_id,
            "engine": "opa_gatekeeper",
            "install_command": "helm install gatekeeper gatekeeper/gatekeeper -n gatekeeper-system --create-namespace",
            "namespace_yaml": _to_yaml(config),
            "helm_repo": "https://open-policy-agent.github.io/gatekeeper/charts",
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Policy-as-Code Generator")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--engine", choices=["kyverno", "opa"], default="kyverno",
                        help="Policy engine (kyverno or opa)")
    parser.add_argument("--output", help="Output directory for policy files")
    parser.add_argument("--admission-config", action="store_true",
                        help="Generate admission controller config")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.admission_config:
        result = generate_admission_config(args.project_id, args.engine)
    elif args.engine == "kyverno":
        result = generate_kyverno_policies(args.project_id)
    else:
        result = generate_opa_policies(args.project_id)

    if args.output and "yaml_content" in result:
        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        engine = result.get("engine", args.engine)
        out_file = out_dir / f"devsecops-{engine}-policies.yaml"
        out_file.write_text(result["yaml_content"], encoding="utf-8")
        result["output_file"] = str(out_file)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            engine = result.get("engine", args.engine)
            print(f"Project: {result['project_id']}")
            print(f"Engine: {engine}")
            if "policy_count" in result:
                print(f"Policies: {result['policy_count']}")
                for p in result.get("policies", []):
                    print(f"  - {p}")
            elif "template_count" in result:
                print(f"Templates: {result['template_count']}")
                print(f"Constraints: {result['constraint_count']}")
                for t in result.get("templates", []):
                    print(f"  Template: {t}")
                for c in result.get("constraints", []):
                    print(f"  Constraint: {c}")
            if args.output:
                print(f"Output: {result.get('output_file', 'N/A')}")


if __name__ == "__main__":
    main()
