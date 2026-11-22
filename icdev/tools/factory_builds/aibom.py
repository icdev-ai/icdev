# CUI // SP-CTI
"""A model build's AI-BOM, linked to its fine-tuning registry promotion (icx-ent-03).

A model is built only from the fine-tuning registry (``tools/finetune/model_registry.py``): the target names a
``model_name``; the build takes its newest version whose status is ``promoted`` AND whose newest row in the
append-only ``ft_promotion_log`` is a promotion (``promoted``, ``auto_promoted`` or ``override_promoted``), never one
demoted since. Anything else is refused (``not_promoted``): an unpromoted adapter is not a release candidate.

The AI-BOM is CycloneDX 1.7 (the vendored, pinned schema): the root component is the fine-tuned model, of type
``machine-learning-model``, with its weights' model-signing root digest, a ``modelCard`` (the dataset it was tuned on
by reference, the registry's evaluation scores as performance metrics, each said to be the registry's own), and
properties that name the registry rows it rests on -- the model version, the training job, the promotion log row and
its action, who promoted it, for which function and when. An ``override_promoted`` promotion (thresholds bypassed) is
stated as one, never smoothed into ``promoted``. The base model is a ``machine-learning-model`` component, the
dataset a ``data`` component, and the inference runtime the target declares are libraries; ``dependencies`` say which
rests on which. ICDEV[OS] admits a model only with an AI-BOM (``no_aibom``) and its weights signed with OpenSSF
model-signing.

The weights files are hashed where the registry says they are, only under the reviewed ``models_root``: regular files
with one link, no symlink on the way, at most ``max_files``. Their digests ARE what model-signing signs; the weights
never leave.
"""
from __future__ import annotations

import hashlib
import math
import os
import stat
import uuid
from pathlib import Path

from tools.factory_builds.config import ConfigError
from tools.factory_builds.constants import DEMOTED, MARKINGS, ML_MODEL, PROMOTED

_ORDER = {m: i for i, m in enumerate(MARKINGS)}


class AibomError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _conn():
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection()


def _rows(conn, sql: str, params: tuple, cols: tuple) -> list[dict]:
    return [dict(zip(cols, tuple(r))) for r in conn.execute(sql, params).fetchall()]


def promoted_version(model_name: str, conn=None) -> dict:
    """``{version, job, dataset, promotion, promotions}`` of the newest promoted version, or :class:`AibomError`."""
    own = conn is None
    conn = conn or _conn()
    try:
        vcols = ("id", "job_id", "model_name", "version", "base_model", "adapter_path", "adapter_hash", "eval_bleu",
                 "eval_rouge_l", "eval_perplexity", "status", "classification", "tenant_id")
        versions = _rows(conn, f"SELECT {', '.join(vcols)} FROM ft_model_versions WHERE model_name = %s "  # nosec B608
                               "ORDER BY version DESC", (model_name,), vcols)
        for v in versions:
            if v["status"] != "promoted":
                continue
            pcols = ("id", "action", "function_name", "actor", "reason", "created_at")
            log = _rows(conn, f"SELECT {', '.join(pcols)} FROM ft_promotion_log "  # nosec B608
                              "WHERE model_version_id = %s ORDER BY id", (v["id"],), pcols)
            if not log or log[-1]["action"] not in PROMOTED:
                continue
            jcols = ("id", "dataset_id", "provider", "base_model")
            job = next(iter(_rows(conn, f"SELECT {', '.join(jcols)} FROM ft_training_jobs WHERE id = %s",  # nosec B608
                                  (v["job_id"],), jcols)), None)
            dcols = ("id", "name", "version", "content_hash", "example_count", "classification")
            ds = next(iter(_rows(conn, f"SELECT {', '.join(dcols)} FROM ft_datasets WHERE id = %s",  # nosec B608
                                 ((job or {}).get("dataset_id"),), dcols)), None) if job else None
            return {"version": v, "job": job, "dataset": ds, "promotion": log[-1], "promotions": log}
    finally:
        if own:
            conn.close()
    raise AibomError("not_promoted", f"no version of {model_name} is promoted in the fine-tuning registry (a demoted or "
                                     "unpromoted adapter is not built)")


def still_promoted(model_version_id: str, promotion_id, conn=None) -> bool:
    """Whether the promotion a build rests on is still the version's newest promotion-log row (nothing demoted it)."""
    own = conn is None
    conn = conn or _conn()
    try:
        log = _rows(conn, "SELECT id, action FROM ft_promotion_log WHERE model_version_id = %s ORDER BY id",
                    (model_version_id,), ("id", "action"))
    finally:
        if own:
            conn.close()
    return bool(log) and str(log[-1]["id"]) == str(promotion_id) and log[-1]["action"] in PROMOTED \
        and not any(r["action"] in DEMOTED and int(r["id"]) > int(promotion_id) for r in log)


def weights(adapter_path: str, models_root: Path, *, max_files: int) -> list[dict]:
    """``[{name, digest}]`` of every weights file under the version's adapter path (relative names)."""
    if not adapter_path:
        raise AibomError("no_weights", "the model version names no adapter path")
    base = Path(adapter_path)
    base = base if base.is_absolute() else models_root / base
    p = Path(os.path.abspath(base))
    if not p.is_relative_to(models_root) or p == models_root:
        raise AibomError("weights_outside_root", "the model's weights are not under the reviewed models_root")
    cur = models_root
    for part in p.relative_to(models_root).parts:
        cur = cur / part
        if cur.is_symlink():
            raise AibomError("weights_symlink", "a symlink is never followed to the weights")
    if not p.is_dir():
        raise AibomError("no_weights", "the model's adapter directory is not there")
    out = []
    for dirpath, dirnames, filenames in os.walk(p, followlinks=False):
        for d in dirnames:
            if (Path(dirpath) / d).is_symlink():
                raise AibomError("weights_symlink", "a symlink is never followed to the weights")
        for f in sorted(filenames):
            fp = Path(dirpath) / f
            try:
                fd = os.open(fp, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            except OSError as exc:      # ELOOP: a symlink; anything else: unreadable
                raise AibomError("weights_file", "a weights file is a symlink or cannot be read") from exc
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                    raise AibomError("weights_file", "a weights file is not a regular file with one link")
                h = hashlib.sha256()
                with os.fdopen(fd, "rb", closefd=False) as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            finally:
                os.close(fd)
            rel = fp.relative_to(p).as_posix()
            out.append({"name": rel, "digest": h.hexdigest()})
            if len(out) > max_files:
                raise AibomError("weights_too_many", f"more than {max_files} weights files")
    if not out:
        raise AibomError("no_weights", "the model's adapter directory holds no files")
    return sorted(out, key=lambda r: tuple(r["name"].split("/")))


def check_binding(found: dict, target: dict) -> None:
    """The registry rows must be the target's org and never above its marking (marking never widens)."""
    v = found["version"]
    if str(v.get("tenant_id") or "").strip() and str(v["tenant_id"]).strip() != str(target["org"]).strip():
        raise AibomError("other_org", "the model version belongs to another tenant than the target's org")
    for row in (v, found.get("dataset") or {}):
        m = str(row.get("classification") or "").strip().upper()
        if row and (m not in _ORDER or _ORDER[m] > _ORDER[target["marking"]]):
            raise AibomError("marking", "the model or its dataset is marked above the target (or unmarked): marking "
                                        "never widens")


def _metric(name: str, value) -> dict | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return {"type": name, "value": f"{f:g}", "slice": "the fine-tuning registry's own evaluation"}


def build(found: dict, target: dict, *, subject_digest: str, version: str, at: str) -> dict:
    """The CycloneDX 1.7 AI-BOM (unvalidated; the caller validates against the pinned schema)."""
    v, job, ds, promo = found["version"], found.get("job") or {}, found.get("dataset"), found["promotion"]
    hexd = subject_digest.split(":", 1)[1]
    base_ref, data_ref = "base-model", "dataset"
    metrics = [m for m in (_metric("bleu", v.get("eval_bleu")), _metric("rouge-l", v.get("eval_rouge_l")),
                           _metric("perplexity", v.get("eval_perplexity"))) if m]
    card: dict = {"modelParameters": {"approach": {"type": "supervised"}, "task": "text-generation",
                                      "datasets": [{"ref": data_ref}] if ds else []}}
    if metrics:
        card["quantitativeAnalysis"] = {"performanceMetrics": metrics}
    override = any(p["action"] == "override_promoted" for p in found.get("promotions") or [])
    props = [("icdev:finetune:model_version_id", v["id"]), ("icdev:finetune:model_version", v["version"]),
             ("icdev:finetune:training_job", job.get("id") or "unknown"),
             ("icdev:finetune:provider", job.get("provider") or "unknown"),
             ("icdev:finetune:promotion_log_id", promo["id"]), ("icdev:finetune:promotion_action", promo["action"]),
             ("icdev:finetune:promoted_by", promo.get("actor") or "unknown"),
             ("icdev:finetune:promoted_for", promo.get("function_name") or "unknown"),
             ("icdev:finetune:promoted_at", str(promo.get("created_at") or "")),
             ("icdev:finetune:thresholds_bypassed", "true" if override else "false"),
             ("icdev:finetune:adapter_hash_claimed", v.get("adapter_hash") or "none")]
    root = {"type": ML_MODEL, "bom-ref": "model", "name": target["subject_name"], "version": version,
            "hashes": [{"alg": "SHA-256", "content": hexd}], "modelCard": card,
            "description": "The fine-tuned model; its hash is the OpenSSF model-signing root digest of its weights",
            "properties": [{"name": k, "value": str(val)} for k, val in props]}
    comps = [{"type": ML_MODEL, "bom-ref": base_ref, "name": str(v.get("base_model") or job.get("base_model") or
                                                                  "unknown"),
              "description": "The base model the adapter was tuned from, as the registry names it"}]
    deps = [{"ref": "model", "dependsOn": [base_ref] + ([data_ref] if ds else [])}, {"ref": base_ref, "dependsOn": []}]
    if ds:
        comps.append({"type": "data", "bom-ref": data_ref, "name": str(ds["name"]), "version": str(ds["version"]),
                      **({"hashes": [{"alg": "SHA-256", "content": ds["content_hash"]}]}
                         if len(str(ds.get("content_hash") or "")) == 64 else {}),
                      "data": [{"type": "dataset", "name": str(ds["name"]),
                                "classification": str(ds.get("classification") or "")}],
                      "properties": [{"name": "icdev:finetune:dataset_id", "value": str(ds["id"])},
                                     {"name": "icdev:finetune:examples", "value": str(ds.get("example_count") or 0)}]})
        deps.append({"ref": data_ref, "dependsOn": []})
    for i, r in enumerate(target.get("runtime") or []):
        ref = f"runtime-{i}"
        comps.append({"type": "library", "bom-ref": ref, "name": str(r["name"]), "version": str(r.get("version") or ""),
                      **({"purl": str(r["purl"])} if r.get("purl") else {})})
        deps[0]["dependsOn"].append(ref)
        deps.append({"ref": ref, "dependsOn": []})
    serial = uuid.UUID(hashlib.sha256(f"{hexd}:{version}".encode()).hexdigest()[:32], version=4)
    return {"bomFormat": "CycloneDX", "specVersion": "1.7", "serialNumber": f"urn:uuid:{serial}", "version": 1,
            "metadata": {"timestamp": at, "component": root,
                         "tools": {"components": [{"type": "application", "name": "icdev-it factory_builds"}]}},
            "components": comps, "dependencies": deps}


__all__ = ["AibomError", "ConfigError", "build", "check_binding", "promoted_version", "still_promoted", "weights"]
