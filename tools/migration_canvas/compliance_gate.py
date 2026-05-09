# CUI // SP-CTI
"""Migration Canvas Compliance Gate.

Deterministic IL-level / target-environment / framework checks for migration scenarios.
No LLM, no external calls.
"""
from __future__ import annotations

from tools.migration_canvas.constants import IL_LEVEL_REQUIREMENTS

_GOVCLOUD_KEYWORDS = ("govcloud", "government", "gov cloud")
_COMMERCIAL_KEYWORDS = ("commercial",)


def _classify_env(target_env: str) -> tuple[bool, bool, bool]:
    """Return (is_govcloud, is_commercial, is_dod)."""
    env = (target_env or "").lower()
    is_govcloud = any(k in env for k in _GOVCLOUD_KEYWORDS)
    is_commercial = any(k in env for k in _COMMERCIAL_KEYWORDS) and not is_govcloud
    is_dod = is_govcloud or any(k in env for k in ("dod", "sipr"))
    return is_govcloud, is_commercial, is_dod


def check_migration_compliance(
    il_level: str,
    target_env: str,
    migration_type: str | None = None,
    frameworks: list[str] | None = None,
) -> dict:
    """Check migration compliance for IL level, target environment, and migration type.

    Args:
        il_level: Impact level — 'IL2', 'IL4', 'IL5', 'IL6', or unknown value.
        target_env: Target environment — 'govcloud', 'commercial', 'dod', etc.
        migration_type: Optional migration type — 'p2c', 'p2v_cloud', etc.
        frameworks: Declared compliance frameworks. When provided, checked for
                    required items (e.g. 'fedramp' for GovCloud). When None,
                    framework-completeness checks are skipped.

    Returns:
        {
            "proceed": bool,             # False only on BLOCK status
            "status": "block"|"warn"|"pass",
            "findings": [...],           # non-empty on warn/block
            "frameworks_applied": [...], # nist_800_53 always present
        }
    """
    findings: list[dict] = []
    # NIST 800-53 is the baseline framework for all IL levels (IL2 through IL6)
    frameworks_applied: list[str] = ["nist_800_53"]

    is_govcloud, is_commercial, is_dod = _classify_env(target_env)
    il_upper = (il_level or "").upper().strip()
    il_reqs = IL_LEVEL_REQUIREMENTS.get(il_upper)

    if il_reqs is None:
        severity = "warn"
        if is_dod or is_govcloud:
            msg = (
                f"Unknown IL level '{il_level}' targeting DoD/GovCloud environment — "
                "verify authorization before proceeding."
            )
        else:
            msg = f"Unknown IL level '{il_level}' — compliance posture cannot be determined."
        findings.append({"rule": "CGT-004", "message": msg, "severity": severity})
    else:
        if is_commercial and not il_reqs.get("commercial_ok", True):
            findings.append({
                "rule": "CGT-001",
                "message": (
                    f"{il_upper} workloads cannot be hosted in commercial cloud. "
                    "GovCloud or on-prem deployment is required."
                ),
                "severity": "block",
            })

        if is_govcloud:
            frameworks_applied.append("fedramp")
            if il_upper in ("IL5", "IL6"):
                frameworks_applied.append("disa_stig")

    # Framework-completeness: only when caller explicitly declares a frameworks list
    if frameworks is not None and is_govcloud and "fedramp" not in frameworks:
        findings.append({
            "rule": "CGT-002",
            "message": (
                "GovCloud target requires FedRAMP authorization. "
                "Add 'fedramp' to the declared frameworks list."
            ),
            "severity": "warn",
        })

    if any(f["severity"] == "block" for f in findings):
        status = "block"
        proceed = False
    elif findings:
        status = "warn"
        proceed = True
    else:
        status = "pass"
        proceed = True

    return {
        "proceed": proceed,
        "status": status,
        "findings": findings,
        "frameworks_applied": list(dict.fromkeys(frameworks_applied)),
    }
