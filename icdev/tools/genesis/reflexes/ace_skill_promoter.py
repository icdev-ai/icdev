# CUI // SP-CTI
"""Genesis reflex: ace_skill_promoter — SIPA-gated ACE skill candidate promotion.

Thin proxy to ``icdev.tools.ace.skill_promoter.run`` so the genesis daemon can
dispatch this as ``tools.genesis.reflexes.ace_skill_promoter.run``.

Cadence: daily (24 h).
Assesses up to 5 pending ``ace_skill_candidates`` per run via SIPA Mode B.
Approved candidates are written to ``args/ace/roles/candidates/`` for human review.
"""
from __future__ import annotations

from typing import Any

IMPLEMENTATION_STATUS = "full"
CADENCE_HOURS = 24


def run(config: dict[str, Any], trust: Any) -> dict[str, Any]:
    """Delegate to icdev.tools.ace.skill_promoter.run."""
    try:
        from icdev.tools.ace.skill_promoter import run as _promote

        result = _promote(config, trust)
        return {
            "success": True,
            "metric_value": float(result.get("promoted", 0)),
            "details": result,
        }
    except Exception as exc:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }


if __name__ == "__main__":
    import json

    print(json.dumps(run({}, None), indent=2))
