# CUI // SP-CTI
"""Cloud Application Migration constants.

All values loaded from context/migration/service_mappings.yaml at import time.
Falls back to hardcoded defaults if the file is missing (air-gap safe).
Never hardcode AWS service names, SOP categories, or status values in route handlers.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SERVICE_MAPPINGS_PATH = _ROOT / "context" / "migration" / "service_mappings.yaml"


def _load_service_mappings() -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
        with open(_SERVICE_MAPPINGS_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return {k: v for k, v in (data.get("mappings") or {}).items()}
    except Exception:
        return {}


SERVICE_MAPPINGS: dict = _load_service_mappings()

# Canvas → SOP table name
CANVAS_SOP_TABLES: dict[str, str] = {
    "mc":  "mc_sops",
    "ddc": "ddc_sops",
    "idc": "idc_sops",
    "ndc": "ndc_sops",
}

# Canvas → SOP primary-key column name
CANVAS_SOP_ID_COL: dict[str, str] = {
    "mc":  "id",
    "ddc": "id",
    "idc": "id",
    "ndc": "sop_id",
}

# Canvas → SOP steps column name
CANVAS_SOP_STEPS_COL: dict[str, str] = {
    "mc":  "steps",
    "ddc": "steps_json",
    "idc": "steps",
    "ndc": "steps",
}

AI_SERVICE_LABELS: dict[str, str] = {
    "bedrock-kb":             "Bedrock Knowledge Bases",
    "bedrock-agents":         "Bedrock Inline Agents",
    "bedrock-claude":         "Bedrock — Claude (NLQ / Text-to-SQL)",
    "bedrock-data-automation": "Bedrock Data Automation",
    "pgvector":               "Aurora pgvector (Semantic Search)",
    "semantic-cache":         "ElastiCache Semantic Cache",
    "amazon-q":               "Amazon Q Developer / in Connect",
    "sagemaker":              "Amazon SageMaker",
    "comprehend":             "Amazon Comprehend",
    "kendra":                 "Amazon Kendra",
}

PROJECT_STATUSES = ("draft", "in_review", "approved", "active", "complete", "archived")
PHASE_STATUSES   = ("planned", "in_progress", "completed", "rolled_back")
AI_OPP_STATUSES  = ("identified", "scoped", "in_progress", "complete", "deferred")
BENEFIT_CATEGORIES = ("search", "nlq", "pipeline", "caching", "ux", "analytics", "security", "efficiency")

STRATEGIES_7R = (
    "rehost", "replatform", "refactor",
    "rearchitect", "repurchase", "retire", "retain",
)
