# CUI // SP-CTI
"""SYNTHETIC inputs for the signed factory builds (icx-ent-03): the tests, the e2e seed and a demonstration.

Writes, under the reviewed ``artefact_root`` (``.tmp/factory-builds`` by default, git-ignored), the inputs of the two
labelled synthetic targets in ``args/factory_builds.yaml`` -- an OCI image layout with one manifest, a project whose
requirements the SBOM generator resolves, a factory scan -- and, under ``models_root``, a model's weights files, plus
the fine-tuning registry rows of a promoted synthetic model version (a dataset, a training job, the version, and its
promotion in the append-only promotion log). Nothing here describes a real system, product, model or person: every
name says ``syn``, the vulnerability ids are ``SYN-ENT03-*``, and the promoter is a named synthetic account.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.factory_builds import config as cmod

MODEL_NAME = "syn-ent03-defect-classifier"
#: A synthetic finding id (no real advisory), and the component the synthetic project's SBOM lists it against.
FINDING = "SYN-ENT03-0001"


def _write(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def image_inputs(base: Path, tag: str = "1") -> dict:
    """``base`` is ``artefact_root/syn-ent03/portal``: an OCI layout, a project and a scan."""
    manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                      "digest": "sha256:" + hashlib.sha256(f"syn config {tag}".encode()).hexdigest(),
                                      "size": 2},
                           "layers": [], "annotations": {"org.opencontainers.image.title": f"syn-ent03 portal {tag}"}},
                          sort_keys=True, separators=(",", ":")).encode()
    d = hashlib.sha256(manifest).hexdigest()
    _write(base / "oci" / "blobs" / "sha256" / d, manifest)
    _write(base / "oci" / "oci-layout", b'{"imageLayoutVersion":"1.0.0"}')
    _write(base / "oci" / "index.json", json.dumps({"schemaVersion": 2, "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": f"sha256:{d}",
         "size": len(manifest)}]}).encode())
    _write(base / "src" / "requirements.txt", b"requests==2.31.0\npyyaml==6.0.1\n")
    _write(base / "scan.json", json.dumps([{"id": FINDING, "component": "requests", "severity": "medium"}]).encode())
    return {"digest": f"sha256:{d}"}


def model_inputs(models_root: Path, scan_base: Path, version: int = 3, model_name: str = MODEL_NAME) -> dict:
    """Weights files under ``models_root/<model>/v<version>`` and the model's scan under ``scan_base``."""
    wdir = models_root / model_name / f"v{version}"
    for i in range(3):
        _write(wdir / "weights" / f"part-{i}.safetensors", f"synthetic weights {version}.{i}".encode())
    _write(wdir / "adapter_config.json", b'{"synthetic": true}')
    _write(scan_base / "scan.json",
           json.dumps([{"id": "SYN-ENT03-0002", "component": "torch", "severity": "low"}]).encode())
    return {"adapter_path": f"{model_name}/v{version}"}


def registry_rows(conn, *, adapter_path: str, version: int = 3, tenant: str = "syn-ent03-org",
                  promoter: str = "syn-ent03-ml-lead", action: str = "promoted",
                  model_name: str = MODEL_NAME) -> dict:
    """The fine-tuning registry rows of a promoted synthetic model version (labelled)."""
    tag = f"{model_name}-v{version}"
    ds, job, mv = f"ds-{tag}", f"job-{tag}", f"mv-{tag}"
    conn.execute("INSERT INTO ft_datasets (id, name, description, purpose, version, example_count, content_hash, "
                 "classification, tenant_id, created_by, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (ds, "syn-ent03 defect notes", "SYNTHETIC (icx-ent-03)", "custom", 1, 120,
                  hashlib.sha256(b"syn-ent03 dataset").hexdigest(), "CUI", tenant, promoter, "ready"))
    conn.execute("INSERT INTO ft_training_jobs (id, dataset_id, provider, base_model, status, classification, "
                 "tenant_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                 (job, ds, "unsloth_local", "syn-base-1b", "completed", "CUI", tenant, promoter))
    conn.execute("INSERT INTO ft_model_versions (id, job_id, model_name, version, base_model, adapter_path, "
                 "adapter_hash, eval_bleu, eval_rouge_l, eval_perplexity, status, classification, tenant_id) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                 (mv, job, model_name, version, "syn-base-1b", adapter_path, "", 0.41, 0.52, 6.3, "promoted", "CUI",
                  tenant))
    conn.execute("INSERT INTO ft_promotion_log (model_version_id, action, function_name, previous_model, reason, "
                 "actor, tenant_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                 (mv, action, "code_generation", "", "SYNTHETIC (icx-ent-03): met the thresholds", promoter, tenant))
    conn.commit()
    return {"model_version_id": mv}


def write_all(cfg: dict | None = None, conn=None, *, tag: str = "1") -> dict:
    """Every synthetic input of the configured synthetic targets; the registry rows only with ``conn``."""
    cfg = cfg or cmod.load()
    root, models = cmod.root(cfg), cmod.root(cfg, "models_root")
    out = {"image": image_inputs(root / "syn-ent03" / "portal", tag)}
    m = model_inputs(models, root / "syn-ent03" / "classifier")
    out["model"] = m
    if conn is not None:
        out["registry"] = registry_rows(conn, adapter_path=m["adapter_path"])
    return out


__all__ = ["FINDING", "MODEL_NAME", "image_inputs", "model_inputs", "registry_rows", "write_all"]
