# CUI // SP-CTI
"""Agentic AI Design Canvas — Model provenance chain tracker (Phase 3).

Extracts model source, training data, version, and license from node props.
Surfaces compliance flags: proprietary model, GPL license, missing training data.
"""

from __future__ import annotations

from tools.agentic_ai_canvas.constants import MODEL_NODES

_PROPRIETARY = {"openai", "anthropic", "google", "microsoft", "amazon", "cohere",
                "gpt", "claude", "gemini", "palm", "mistral-api"}

_OPEN_SOURCE = {"llama", "mistral", "falcon", "bloom", "pythia", "qwen", "phi",
                "gemma", "olmo", "deepseek", "yi-", "solar-"}

PROVENANCE_FIELDS = ["model_source", "training_data", "model_version", "model_license"]


def extract_provenance_chain(nodes: list[dict]) -> list[dict]:
    """Return provenance metadata for every model-type node."""
    chain: list[dict] = []
    for n in nodes:
        if n.get("type", "") in MODEL_NODES:
            props = n.get("props") or {}
            chain.append({
                "node_id": n["id"],
                "label": n.get("label", n["id"]),
                "type": n.get("type", ""),
                "model_source": props.get("model_source", ""),
                "training_data": props.get("training_data", ""),
                "model_version": props.get("model_version", ""),
                "model_license": props.get("model_license", ""),
            })
    return chain


def get_compliance_flags(provenance_chain: list[dict]) -> list[dict]:
    """Return compliance flags derived from provenance metadata."""
    flags: list[dict] = []
    for entry in provenance_chain:
        src = entry.get("model_source", "").lower()
        lic = entry.get("model_license", "").lower()
        lbl = entry["label"]
        nid = entry["node_id"]

        if any(ind in src for ind in _PROPRIETARY):
            flags.append({
                "node_id": nid,
                "label": lbl,
                "flag": "proprietary-model",
                "severity": "INFO",
                "message": (
                    f"Proprietary model: {entry['model_source']} — "
                    "verify FedRAMP / DoD authorization before deployment."
                ),
            })

        if lic and "gpl" in lic:
            flags.append({
                "node_id": nid,
                "label": lbl,
                "flag": "gpl-model",
                "severity": "WARN",
                "message": (
                    f"GPL-licensed model '{lbl}' — copyleft terms may apply "
                    "to derivative works and model outputs."
                ),
            })

        if not entry.get("training_data"):
            flags.append({
                "node_id": nid,
                "label": lbl,
                "flag": "missing-training-data",
                "severity": "LOW",
                "message": (
                    f"Model '{lbl}' has no training data documented — "
                    "required for NIST AI RMF MAP-2 traceability."
                ),
            })

        if not entry.get("model_source"):
            flags.append({
                "node_id": nid,
                "label": lbl,
                "flag": "missing-model-source",
                "severity": "LOW",
                "message": (
                    f"Model '{lbl}' has no source documented — "
                    "provenance chain is incomplete."
                ),
            })

    return flags
