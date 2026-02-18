#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""K8s NetworkPolicy Manifest Generator for ZTA Micro-Segmentation.

Generates enhanced Kubernetes NetworkPolicy manifests implementing Zero Trust
Architecture (ZTA) micro-segmentation principles. Produces default-deny ingress
and egress policies per namespace with DNS-only egress allowances, plus
per-pod port-level NetworkPolicies for service-to-service microsegmentation.

ADR D111: Dual-hub crosswalk — network pillar controls (AC-3, AC-4, SC-7, SC-8)
ADR D120: ZTA network pillar — per-pod micro-segmentation at optimal maturity
ADR D121: NetworkPolicy complements service mesh (Istio/Linkerd) for defense-in-depth

Usage:
    python tools/devsecops/network_segmentation_generator.py \\
        --project-path /path/to/project --namespaces app,monitoring --json
    python tools/devsecops/network_segmentation_generator.py \\
        --project-path /path/to/project --services "api:8080,db:5432" --output /out
    python tools/devsecops/network_segmentation_generator.py \\
        --project-path /path/to/project --namespaces app --services "api:8080" --human
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml as _yaml_mod
    _HAS_YAML = True
except ImportError:
    _yaml_mod = None
    _HAS_YAML = False

# ---------------------------------------------------------------------------
# Classification header
# ---------------------------------------------------------------------------
_CUI_HEADER = (
    "# CUI // SP-CTI\n"
    "# CONTROLLED UNCLASSIFIED INFORMATION\n"
    "# Authorized for: Internal project use only\n"
    "# Generated: {timestamp}\n"
    "# Generator: ICDEV Network Segmentation Generator\n"
    "# NIST 800-53: AC-3, AC-4, SC-7, SC-8, SC-13\n"
    "# ZTA Pillar: Network (micro-segmentation)\n"
    "# CUI // SP-CTI\n"
)

_ZTA_LABELS = {
    "icdev.mil/component": "zta-network-segmentation",
    "icdev.mil/classification": "CUI",
    "app.kubernetes.io/managed-by": "icdev",
}

# Default service port registry (used when a service name has no declared port)
_DEFAULT_SERVICE_PORTS = {
    "api": 8080,
    "web": 8080,
    "frontend": 3000,
    "backend": 8080,
    "db": 5432,
    "postgres": 5432,
    "mysql": 3306,
    "redis": 6379,
    "kafka": 9092,
    "elasticsearch": 9200,
    "prometheus": 9090,
    "grafana": 3000,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cui_header() -> str:
    """Return formatted CUI header with current UTC timestamp."""
    return _CUI_HEADER.format(timestamp=datetime.now(timezone.utc).isoformat())


def _to_yaml(data: dict) -> str:
    """Serialize dict to YAML string. Falls back to JSON if PyYAML unavailable."""
    if _HAS_YAML and _yaml_mod is not None:
        try:
            return _yaml_mod.dump(data, default_flow_style=False, sort_keys=False)
        except Exception:
            pass
    return json.dumps(data, indent=2)


def _write_manifest(output_dir: Path, filename: str, content: str) -> Path:
    """Write manifest file to output directory, creating parents as needed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    target.write_text(content, encoding="utf-8")
    return target


def _zta_labels(extra: dict = None) -> dict:
    """Return ZTA label dict merged with any extra labels."""
    labels = dict(_ZTA_LABELS)
    if extra:
        labels.update(extra)
    return labels


def _load_config() -> dict:
    """Load ZTA configuration from args/zta_config.yaml.

    Returns the parsed YAML config dict if available, otherwise returns a
    minimal fallback config containing the network pillar definition with
    relevant NIST 800-53 controls and microsegmentation evidence types.
    """
    config_path = BASE_DIR / "args" / "zta_config.yaml"
    if _HAS_YAML and _yaml_mod is not None and config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as fh:
                return _yaml_mod.safe_load(fh) or {}
        except Exception:
            pass
    # Minimal fallback — network pillar only
    return {
        "pillars": {
            "network": {
                "weight": 0.15,
                "description": "Micro-segmentation, encrypted channels, software-defined perimeter",
                "nist_800_53_controls": ["AC-3", "AC-4", "SC-7", "SC-8", "SC-13"],
                "evidence_types": [
                    "network_policies_present",
                    "default_deny_egress",
                    "mtls_enforced",
                    "encrypted_transit",
                    "microsegmentation_active",
                ],
            }
        },
        "maturity_levels": {
            "traditional": {"score_range": [0.0, 0.33]},
            "advanced": {"score_range": [0.34, 0.66]},
            "optimal": {"score_range": [0.67, 1.0]},
        },
    }


def _get_db():
    """Open connection to ICDEV SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _parse_services(services_str: str) -> list:
    """Parse comma-separated service definitions into list of (name, port) tuples.

    Accepts formats:
        - "api:8080,db:5432"  — name:port pairs
        - "api,db"            — names only (ports resolved from default registry)
        - mixed               — combination of both formats
    """
    result = []
    if not services_str:
        return result
    for entry in services_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            name, _, port_str = entry.partition(":")
            try:
                port = int(port_str.strip())
            except ValueError:
                port = _DEFAULT_SERVICE_PORTS.get(name.strip().lower(), 8080)
            result.append((name.strip(), port))
        else:
            port = _DEFAULT_SERVICE_PORTS.get(entry.lower(), 8080)
            result.append((entry, port))
    return result


# ---------------------------------------------------------------------------
# generate_namespace_isolation
# ---------------------------------------------------------------------------

def generate_namespace_isolation(project_path: str, namespaces: list) -> dict:
    """Generate default-deny ingress and egress NetworkPolicies per namespace.

    For each namespace in the provided list, produces two NetworkPolicy manifests:

    1. ``default-deny-ingress`` — Selects all pods (empty podSelector) and
       declares policyTypes: [Ingress] with no ingress rules, blocking all
       inbound traffic by default.

    2. ``default-deny-egress`` — Selects all pods (empty podSelector) and
       declares policyTypes: [Egress] with a single DNS exception allowing UDP
       and TCP port 53 to the kube-system namespace, required for cluster DNS
       resolution. All other egress is denied.

    Args:
        project_path: Base path of the project. Manifests are written to
            ``<project_path>/k8s/network-segmentation/``.
        namespaces: List of Kubernetes namespace names to generate policies for.

    Returns:
        dict with keys:
            - ``policies``: list of dicts describing each generated policy
            - ``files``: list of absolute file paths written
            - ``yaml_content``: concatenated YAML of all manifests
            - ``policy_count``: total number of NetworkPolicy objects generated
            - ``namespaces``: the namespace list processed
            - ``generated_at``: ISO-8601 UTC timestamp
    """
    _load_config()  # validate config is accessible

    output_dir = Path(project_path) / "k8s" / "network-segmentation"
    all_yaml_parts = []
    files_written = []
    policies_meta = []

    for ns in namespaces:
        ns = ns.strip()
        if not ns:
            continue

        # --- Default-deny ingress ---
        deny_ingress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "default-deny-ingress",
                "namespace": ns,
                "labels": _zta_labels({
                    "app.kubernetes.io/component": "default-deny-ingress",
                    "icdev.mil/namespace": ns,
                }),
                "annotations": {
                    "icdev.mil/classification": "CUI",
                    "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                    "icdev.mil/nist-controls": "AC-3,AC-4,SC-7",
                    "icdev.mil/purpose": "ZTA default-deny ingress — blocks all inbound traffic",
                },
            },
            "spec": {
                "podSelector": {},
                "policyTypes": ["Ingress"],
            },
        }

        # --- Default-deny egress with DNS exception ---
        deny_egress = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "default-deny-egress",
                "namespace": ns,
                "labels": _zta_labels({
                    "app.kubernetes.io/component": "default-deny-egress",
                    "icdev.mil/namespace": ns,
                }),
                "annotations": {
                    "icdev.mil/classification": "CUI",
                    "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                    "icdev.mil/nist-controls": "AC-3,AC-4,SC-7,SC-8",
                    "icdev.mil/purpose": "ZTA default-deny egress — DNS-only exception to kube-system",
                },
            },
            "spec": {
                "podSelector": {},
                "policyTypes": ["Egress"],
                "egress": [
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system",
                                    }
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    }
                ],
            },
        }

        # Render YAML for both policies
        ingress_yaml = _to_yaml(deny_ingress)
        egress_yaml = _to_yaml(deny_egress)

        # Write ingress policy file
        ingress_filename = f"{ns}-default-deny-ingress.yaml"
        ingress_content = _cui_header() + ingress_yaml
        ingress_path = _write_manifest(output_dir, ingress_filename, ingress_content)
        files_written.append(str(ingress_path))
        all_yaml_parts.append(ingress_content)

        # Write egress policy file
        egress_filename = f"{ns}-default-deny-egress.yaml"
        egress_content = _cui_header() + egress_yaml
        egress_path = _write_manifest(output_dir, egress_filename, egress_content)
        files_written.append(str(egress_path))
        all_yaml_parts.append(egress_content)

        policies_meta.append({
            "namespace": ns,
            "policy_name": "default-deny-ingress",
            "policy_type": "default-deny",
            "direction": "Ingress",
            "file": str(ingress_path),
        })
        policies_meta.append({
            "namespace": ns,
            "policy_name": "default-deny-egress",
            "policy_type": "default-deny-with-dns",
            "direction": "Egress",
            "dns_exception": {"namespace": "kube-system", "ports": [53]},
            "file": str(egress_path),
        })

    return {
        "policies": policies_meta,
        "files": files_written,
        "yaml_content": "\n---\n".join(all_yaml_parts),
        "policy_count": len(policies_meta),
        "namespaces": namespaces,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# generate_microsegmentation
# ---------------------------------------------------------------------------

def generate_microsegmentation(project_path: str, services: list) -> dict:
    """Generate per-pod NetworkPolicies for service-to-service micro-segmentation.

    For each service in the provided list, produces a NetworkPolicy that:

    - Selects pods matching ``app.kubernetes.io/name: <service-name>``
    - Allows ingress only on the declared service port (TCP)
    - Allows egress only on the declared service port to pods with the same
      app label (intra-service communication) plus DNS egress to kube-system
    - Applies ZTA labels and CUI classification annotations

    This implements the "optimal" maturity level for the ZTA Network pillar
    (per-pod micro-segmentation, port-level restrictions).

    Args:
        project_path: Base path of the project. Manifests are written to
            ``<project_path>/k8s/network-segmentation/``.
        services: List of ``(service_name, port)`` tuples. Use
            ``_parse_services()`` to convert from CLI input strings.

    Returns:
        dict with keys:
            - ``policies``: list of dicts describing each generated policy
            - ``files``: list of absolute file paths written
            - ``yaml_content``: concatenated YAML of all manifests
            - ``policy_count``: total number of NetworkPolicy objects generated
            - ``services``: list of service dicts ``{name, port}``
            - ``generated_at``: ISO-8601 UTC timestamp
    """
    _load_config()  # validate config is accessible

    output_dir = Path(project_path) / "k8s" / "network-segmentation"
    all_yaml_parts = []
    files_written = []
    policies_meta = []

    for svc_name, svc_port in services:
        svc_name = svc_name.strip()
        if not svc_name:
            continue

        policy_name = f"{svc_name}-microseg"

        netpol = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "labels": _zta_labels({
                    "app.kubernetes.io/component": "microsegmentation",
                    "app.kubernetes.io/name": svc_name,
                    "icdev.mil/service": svc_name,
                }),
                "annotations": {
                    "icdev.mil/classification": "CUI",
                    "icdev.mil/generated": datetime.now(timezone.utc).isoformat(),
                    "icdev.mil/nist-controls": "AC-3,AC-4,SC-7,SC-8,SC-13",
                    "icdev.mil/purpose": (
                        f"ZTA micro-segmentation for {svc_name} — "
                        f"port-level ingress/egress restricted to port {svc_port}"
                    ),
                    "icdev.mil/zta-maturity": "optimal",
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": svc_name,
                    }
                },
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "ports": [
                            {"protocol": "TCP", "port": svc_port},
                        ],
                    }
                ],
                "egress": [
                    # Allow egress to same-labeled pods on the service port
                    {
                        "to": [
                            {
                                "podSelector": {
                                    "matchLabels": {
                                        "app.kubernetes.io/name": svc_name,
                                    }
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "TCP", "port": svc_port},
                        ],
                    },
                    # DNS exception — required for cluster service discovery
                    {
                        "to": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system",
                                    }
                                }
                            }
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                ],
            },
        }

        policy_yaml = _to_yaml(netpol)
        filename = f"{svc_name}-microseg.yaml"
        content = _cui_header() + policy_yaml
        written_path = _write_manifest(output_dir, filename, content)
        files_written.append(str(written_path))
        all_yaml_parts.append(content)

        policies_meta.append({
            "service": svc_name,
            "port": svc_port,
            "policy_name": policy_name,
            "policy_type": "microsegmentation",
            "ingress_ports": [svc_port],
            "egress_ports": [svc_port, 53],
            "file": str(written_path),
        })

    return {
        "policies": policies_meta,
        "files": files_written,
        "yaml_content": "\n---\n".join(all_yaml_parts),
        "policy_count": len(policies_meta),
        "services": [{"name": n, "port": p} for n, p in services],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate enhanced K8s NetworkPolicy manifests for ZTA micro-segmentation. "
            "Produces default-deny ingress/egress policies per namespace and per-pod "
            "port-level NetworkPolicies per service."
        )
    )
    parser.add_argument(
        "--project-path",
        required=True,
        help="Base project path. Manifests written to <path>/k8s/network-segmentation/",
    )
    parser.add_argument(
        "--namespaces",
        default="",
        help="Comma-separated namespace names for default-deny isolation (e.g. app,monitoring)",
    )
    parser.add_argument(
        "--services",
        default="",
        help=(
            "Comma-separated service definitions for micro-segmentation. "
            "Format: name:port or name (port resolved from defaults). "
            "Example: api:8080,db:5432,redis:6379"
        ),
    )
    parser.add_argument(
        "--output",
        default="",
        help="Override output directory (default: <project-path>/k8s/network-segmentation/)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="Output human-readable summary (default when --json not specified)",
    )
    args = parser.parse_args()

    project_path = args.project_path
    if args.output:
        # Allow caller to override where manifests land by temporarily adjusting
        # project_path so that the output_dir resolves correctly. The functions
        # append k8s/network-segmentation/ themselves.
        project_path = str(Path(args.output).parent.parent)

    namespaces = [n.strip() for n in args.namespaces.split(",") if n.strip()] if args.namespaces else []
    services = _parse_services(args.services) if args.services else []

    combined = {
        "isolation": None,
        "microsegmentation": None,
        "total_policy_count": 0,
        "total_files": [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if namespaces:
        isolation_result = generate_namespace_isolation(project_path, namespaces)
        combined["isolation"] = isolation_result
        combined["total_policy_count"] += isolation_result["policy_count"]
        combined["total_files"].extend(isolation_result["files"])

    if services:
        microseg_result = generate_microsegmentation(project_path, services)
        combined["microsegmentation"] = microseg_result
        combined["total_policy_count"] += microseg_result["policy_count"]
        combined["total_files"].extend(microseg_result["files"])

    if not namespaces and not services:
        combined["warning"] = "No --namespaces or --services provided. Nothing generated."

    use_json = args.json or not args.human

    if use_json:
        print(json.dumps(combined, indent=2))
    else:
        # Human-readable output
        print("=" * 70)
        print("ICDEV Network Segmentation Generator — ZTA Micro-Segmentation")
        print("Classification: CUI // SP-CTI")
        print("=" * 70)
        print(f"Generated at : {combined['generated_at']}")
        print(f"Project path : {project_path}")
        print(f"Total policies: {combined['total_policy_count']}")
        print(f"Total files  : {len(combined['total_files'])}")

        if combined.get("warning"):
            print(f"\nWARNING: {combined['warning']}")

        if combined.get("isolation"):
            iso = combined["isolation"]
            print(f"\n[Namespace Isolation] {iso['policy_count']} policies across {len(iso['namespaces'])} namespace(s)")
            for ns in iso["namespaces"]:
                print(f"  Namespace: {ns}")
                print("    - default-deny-ingress  (blocks all inbound traffic)")
                print("    - default-deny-egress   (DNS-only exception to kube-system:53)")
            print(f"  Output directory: {Path(iso['files'][0]).parent if iso['files'] else 'n/a'}")

        if combined.get("microsegmentation"):
            mseg = combined["microsegmentation"]
            print(f"\n[Micro-Segmentation] {mseg['policy_count']} policies for {len(mseg['services'])} service(s)")
            for svc in mseg["services"]:
                print(f"  Service: {svc['name']}  port: {svc['port']}/TCP")
                print(f"    - Ingress: port {svc['port']}/TCP only")
                print(f"    - Egress:  port {svc['port']}/TCP (intra-service) + 53/UDP+TCP (DNS)")
            if mseg["files"]:
                print(f"  Output directory: {Path(mseg['files'][0]).parent}")

        print("\n[Files Written]")
        for f in combined["total_files"]:
            print(f"  -> {f}")
        print()


if __name__ == "__main__":
    main()
