# CUI // SP-CTI
"""WriteGuard Style Guide Cascade — content type rubrics + enforcement (D-WG-5a/5b).

Loads style guides from DB (wg_style_guides) with YAML-defined rules, supports
inheritance (project → org → global), and enforces content-type-specific scoring.

Also loads compliance vocabulary from context/writeguard/rubrics/ for
content-type-aware analysis (blog, proposal_technical, proposal_management,
compliance_narrative).

CUI // SP-CTI
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_RUBRIC_DIR = _ROOT / "context" / "writeguard" / "rubrics"

# CUI compliance vocabulary — terms that MUST appear in certain content types
_CUI_REQUIRED_TERMS = {
    "compliance_narrative": [
        "NIST", "control", "authorization", "boundary", "risk",
        "continuous monitoring", "security plan",
    ],
    "proposal_technical": [
        "shall", "compliance", "requirement", "approach", "methodology",
    ],
    "proposal_management": [
        "staffing", "schedule", "risk", "quality", "deliverable",
        "performance", "milestone",
    ],
}

# Terms to flag in specific content types
_CONTENT_TYPE_WARNINGS = {
    "blog": {
        "forbidden": ["shall", "heretofore", "aforementioned", "notwithstanding"],
        "message": "formal/legal terms not appropriate for blog content",
    },
    "proposal_technical": {
        "forbidden": ["gonna", "wanna", "basically", "stuff", "cool", "awesome"],
        "message": "informal language inappropriate for proposal volumes",
    },
    "compliance_narrative": {
        "forbidden": ["maybe", "perhaps", "we think", "we hope", "probably"],
        "message": "hedging language undermines compliance narrative authority",
    },
}


def _load_yaml(path: Path) -> dict:
    """Load YAML file, return empty dict on failure."""
    try:
        import yaml
    except ImportError:
        return {}
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
        return {}


def load_rubric(content_type: str) -> dict:
    """Load a content-type rubric from context/writeguard/rubrics/."""
    path = _RUBRIC_DIR / f"{content_type}.yaml"
    return _load_yaml(path)


def load_style_guide_from_db(scope: str = "global", scope_id: str = "") -> Optional[dict]:
    """Load active style guide from wg_style_guides table."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT guide_yaml, inherits_from FROM wg_style_guides "
                "WHERE scope = ? AND (scope_id = ? OR scope_id IS NULL) "
                "AND is_active = 1 ORDER BY version DESC LIMIT 1",
                (scope, scope_id),
            ).fetchone()
            if row:
                import yaml
                guide = yaml.safe_load(row["guide_yaml"]) or {}
                guide["_inherits_from"] = row["inherits_from"]
                return guide
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Style guide DB lookup failed: %s", exc)
    return None


def resolve_style_cascade(
    project_id: str = "",
    org_id: str = "",
) -> dict:
    """Resolve style guide via cascade: project → org → global → defaults."""
    merged: Dict[str, Any] = {}

    # Level 1: Global defaults
    global_guide = load_style_guide_from_db("global")
    if global_guide:
        merged.update(global_guide)

    # Level 2: Org override
    if org_id:
        org_guide = load_style_guide_from_db("org", org_id)
        if org_guide:
            merged.update(org_guide)

    # Level 3: Project override
    if project_id:
        proj_guide = load_style_guide_from_db("project", project_id)
        if proj_guide:
            merged.update(proj_guide)

    return merged


def check_content_type_compliance(
    text: str,
    content_type: str = "",
) -> Dict[str, Any]:
    """Enforce content-type-specific rules and compliance vocabulary (D-WG-5b).

    Returns:
        score (0-1), issues, missing_terms, forbidden_terms_found, content_type
    """
    if not content_type:
        return {
            "score": 1.0,
            "content_type": "",
            "issues": [],
            "missing_terms": [],
            "forbidden_found": [],
        }

    text_lower = text.lower()
    issues: List[str] = []

    # Check required terms
    required = _CUI_REQUIRED_TERMS.get(content_type, [])
    missing = [t for t in required if t.lower() not in text_lower]
    if missing:
        issues.append(
            f"missing required terms for {content_type}: {', '.join(missing[:5])}"
        )

    # Check forbidden terms
    forbidden_found: List[str] = []
    warnings = _CONTENT_TYPE_WARNINGS.get(content_type, {})
    forbidden_list = warnings.get("forbidden", [])
    for term in forbidden_list:
        if term.lower() in text_lower:
            forbidden_found.append(term)
    if forbidden_found:
        msg = warnings.get("message", "inappropriate terms found")
        issues.append(f"{msg}: {', '.join(forbidden_found[:5])}")

    # Load rubric for dimension weights (informational)
    rubric = load_rubric(content_type)
    rubric_dims = list((rubric.get("dimensions", {}) or {}).keys())

    # Score
    penalty = len(missing) * 0.08 + len(forbidden_found) * 0.12
    score = max(0.0, 1.0 - penalty)

    return {
        "score": round(score, 2),
        "content_type": content_type,
        "rubric_name": rubric.get("name", ""),
        "rubric_dimensions": rubric_dims,
        "missing_terms": missing,
        "forbidden_found": forbidden_found,
        "issues": issues,
    }


def check_style_guide(
    text: str,
    project_id: str = "",
    content_type: str = "",
) -> Dict[str, Any]:
    """Run full style guide + content type compliance check.

    Combines:
    - Style guide cascade (project → org → global)
    - Content-type rubric enforcement
    - CUI compliance vocabulary

    Returns dict with score, issues, style_rules_applied, content_type_result.
    """
    issues: List[str] = []

    # Resolve style guide
    guide = resolve_style_cascade(project_id=project_id)
    rules_applied: List[str] = []

    # Apply guide rules if any
    text_lower = text.lower()
    if guide:
        # Custom forbidden words from guide
        forbidden = guide.get("forbidden_words", [])
        found_forbidden = [w for w in forbidden if w.lower() in text_lower]
        if found_forbidden:
            issues.append(f"style guide violation — forbidden: {', '.join(found_forbidden[:5])}")
            rules_applied.append("forbidden_words")

        # Required sections/headings
        required_headings = guide.get("required_headings", [])
        for heading in required_headings:
            if heading.lower() not in text_lower:
                issues.append(f"missing required section: {heading}")
                rules_applied.append(f"required_heading:{heading}")

        # Max sentence length
        max_sent_len = guide.get("max_sentence_length", 0)
        if max_sent_len > 0:
            sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
            long_sents = [s for s in sentences if len(s.split()) > max_sent_len]
            if long_sents:
                issues.append(
                    f"{len(long_sents)} sentence(s) exceed {max_sent_len}-word limit"
                )
                rules_applied.append("max_sentence_length")

    # Content type compliance
    ct_result = check_content_type_compliance(text, content_type)
    issues.extend(ct_result.get("issues", []))

    # Combined score
    score = max(0.0, 1.0 - len(issues) * 0.10)

    return {
        "score": round(score, 2),
        "guide_loaded": bool(guide),
        "rules_applied": rules_applied,
        "content_type": ct_result,
        "issues": issues,
    }
