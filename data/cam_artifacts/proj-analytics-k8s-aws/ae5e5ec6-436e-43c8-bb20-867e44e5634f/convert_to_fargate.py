from __future__ import annotations
# CUI // SP-CTI
# Convert K8s manifests to Fargate-compatible specs.
# Removes DaemonSet resources, hostPath volumes, privileged containers.
import sys
from pathlib import Path
import yaml

INCOMPATIBLE_KINDS = {"DaemonSet"}
DISALLOWED_VOLUME_TYPES = {"hostPath", "emptyDir"}

def convert_manifest(path: Path) -> dict | None:
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not doc:
        return None
    kind = doc.get("kind", "")
    if kind in INCOMPATIBLE_KINDS:
        print(f"  SKIP {path.name}: {kind} not supported on Fargate")
        return None

    spec = doc.get("spec", {})
    template = spec.get("template", spec)
    pod_spec = template.get("spec", {})

    # Remove hostPath volumes
    volumes = [v for v in pod_spec.get("volumes", [])
               if not any(vtype in v for vtype in DISALLOWED_VOLUME_TYPES)]
    if volumes != pod_spec.get("volumes"):
        pod_spec["volumes"] = volumes
        print(f"  WARN {path.name}: removed incompatible volumes")

    # Remove privileged security contexts
    for container in pod_spec.get("containers", []):
        sc = container.get("securityContext", {})
        if sc.get("privileged"):
            del sc["privileged"]
            print(f"  WARN {path.name}/{container['name']}: removed privileged=true")

    return doc

if __name__ == "__main__":
    src_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "k8s/")
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "k8s-fargate/")
    out_dir.mkdir(parents=True, exist_ok=True)
    for manifest in src_dir.glob("**/*.yaml"):
        result = convert_manifest(manifest)
        if result:
            out_path = out_dir / manifest.relative_to(src_dir)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                yaml.dump(result, fh)
            print(f"  OK  {out_path}")
