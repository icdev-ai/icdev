# CUI // SP-CTI
"""IDR IaC Review — Terraform / Ansible / CloudFormation / Kubernetes analysis.

Called by IDR Stage 2 for 'iac' type uploads (devops domain profile).
Public entry point: review_iac(file_path) -> dict
"""
from __future__ import annotations

import pathlib
import re
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.devops.iac_review")

_MAX_IAC_CHARS = 20_000

# ── IaC Type Detection ────────────────────────────────────────────────────────

_IAC_PATTERNS: list[tuple[str, list[str]]] = [
    ("terraform", [r'^\s*resource\s+"', r'^\s*variable\s+"', r'^\s*terraform\s+\{', r'\.tf$']),
    ("ansible", [r'^\s*-\s+name:', r'^\s*hosts:', r'^\s*tasks:', r'\.ya?ml$.*ansible']),
    ("cloudformation", [r'"AWSTemplateFormatVersion"', r'"Resources"\s*:', r'AWSTemplateFormatVersion:']),
    ("kubernetes", [r'^\s*apiVersion:', r'^\s*kind:\s+(Deployment|Service|Pod|ConfigMap|Secret)']),
    ("helm", [r'^\s*{{-?\s+', r'Chart\.yaml', r'values\.yaml']),
    ("pulumi", [r'import\s+pulumi', r'pulumi\.export\(']),
]


def detect_iac_type(text: str, filename: str) -> str:
    """Return best-guess IaC type from text and filename."""
    fname_lower = filename.lower()
    if fname_lower.endswith(".tf") or fname_lower.endswith(".tfvars"):
        return "terraform"
    if fname_lower in ("chart.yaml", "values.yaml") or ".helm" in fname_lower:
        return "helm"
    if fname_lower.endswith((".yaml", ".yml")) and re.search(r"AWSTemplateFormatVersion", text):
        return "cloudformation"
    if fname_lower.endswith((".yaml", ".yml")) and re.search(r"^\s*apiVersion:", text, re.MULTILINE):
        return "kubernetes"
    for iac_type, patterns in _IAC_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.MULTILINE | re.IGNORECASE):
                return iac_type
    return "unknown"


# ── LLM Prompt ────────────────────────────────────────────────────────────────

_REVIEW_PROMPT = """\
You are a senior DevSecOps engineer reviewing Infrastructure-as-Code ({iac_type}) for an \
enterprise government/defense environment (FedRAMP/CMMC compliance applicable).

File: {filename}

INSTRUCTIONS:
1. Analyze for security gaps, misconfigurations, compliance violations, and optimization opportunities.
2. Return a single JSON object with this exact schema:

{{
  "security_compliance": [
    {{
      "title": "string",
      "severity": "CAT1|CAT2|CAT3|info",
      "detail": "string",
      "remediation": "string",
      "code_snippet": "string (corrected IaC snippet if applicable, else \"\")",
      "references": ["NIST-AC-XX", "CIS-X.Y"]
    }}
  ],
  "optimization": [
    {{
      "title": "string",
      "detail": "string",
      "recommendation": "string",
      "code_snippet": "string"
    }}
  ],
  "remediation": [
    {{
      "title": "string",
      "priority": "high|medium|low",
      "steps": ["step 1", "step 2"],
      "code_snippet": "string",
      "verification": "string"
    }}
  ],
  "summary": "string — concise review summary (≤120 words)",
  "risk_score": "high|medium|low",
  "compliance_gaps": ["NIST-AC-01", "CIS-3.1"]
}}

3. Severity: CAT1 = critical exploit/plaintext secrets/open wildcard perms; \
CAT2 = significant weakness; CAT3 = minor; info = observation.
4. Limit each array to top 5 items. Keep code_snippet ≤20 lines.

IaC content ({iac_type}):
```
{iac_content}
```
"""


def _call_llm(prompt: str) -> str:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        # pdx-hyg-01: the IaC content is USER-UPLOADED, so do NOT skip the router's
        # prompt-injection scan. On a high-confidence detection the router raises
        # (its "block" convention); that surfaces via the except below as an empty
        # response, which _parse_response turns into a safe _fallback review rather
        # than forwarding attacker-controlled content to the model.
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
            temperature=0.1,
        )
        resp = router.invoke("idr_iac_review", req)
        return getattr(resp, "content", "") or ""
    except Exception as exc:
        logger.warning("iac_review LLM call failed: %s", exc)
        return ""


def _parse_response(raw: str, iac_type: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = re.search(r"\{.*\}", text, re.DOTALL)
    candidate = (fence.group(1) if fence else None) or (blob.group() if blob else None)
    if not candidate:
        return _fallback(iac_type, "LLM returned no JSON")
    try:
        data = json_safe_loads(candidate)
    except Exception as exc:
        return _fallback(iac_type, f"JSON parse error: {exc}")

    return {
        "security_compliance": _as_list(data.get("security_compliance")),
        "optimization": _as_list(data.get("optimization")),
        "remediation": _as_list(data.get("remediation")),
        "summary": str(data.get("summary") or ""),
        "risk_score": str(data.get("risk_score") or "medium"),
        "compliance_gaps": list(data.get("compliance_gaps") or []),
    }


def json_safe_loads(text: str) -> dict:
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try closing unclosed brackets
        depth: list[str] = []
        in_str = False
        esc = False
        for ch in text:
            if esc:
                esc = False
                continue
            if in_str:
                if ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in ("{", "["):
                    depth.append(ch)
                elif ch == "}":
                    if depth and depth[-1] == "{":
                        depth.pop()
                elif ch == "]":
                    if depth and depth[-1] == "[":
                        depth.pop()
        suffix = ""
        if in_str:
            suffix += '"'
        for opener in reversed(depth):
            suffix += "}" if opener == "{" else "]"
        return json.loads(text + suffix)


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) if isinstance(item, dict) else {"title": str(item)} for item in value]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _fallback(iac_type: str, reason: str) -> dict[str, Any]:
    return {
        "security_compliance": [{
            "title": "IaC review generated with deterministic fallback",
            "severity": "info",
            "detail": reason,
            "remediation": "Re-run when LLM provider is available.",
            "code_snippet": "",
            "references": [],
        }],
        "optimization": [],
        "remediation": [],
        "summary": f"Automated {iac_type} review unavailable. Manual review required.",
        "risk_score": "unknown",
        "compliance_gaps": [],
    }


# ── Public Entry Point ────────────────────────────────────────────────────────

def review_iac(file_path: str) -> dict[str, Any]:
    """Read an IaC file and run a full LLM-based security/optimization review.

    Called by IDR Stage 2 for 'iac' type uploads (devops domain).
    Returns: {review_id, iac_type, security_compliance, optimization, remediation, summary, ...}
    """
    review_id = str(uuid.uuid4())
    p = pathlib.Path(file_path)

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("review_iac: cannot read %s: %s", file_path, exc)
        result = _fallback("unknown", f"Cannot read file: {exc}")
        result["review_id"] = review_id
        result["iac_type"] = "unknown"
        return result

    iac_type = detect_iac_type(content, p.name)
    prompt = _REVIEW_PROMPT.format(
        iac_type=iac_type,
        filename=p.name,
        iac_content=content[:_MAX_IAC_CHARS],
    )

    raw = _call_llm(prompt)
    parsed = _parse_response(raw, iac_type)

    logger.info(
        "review_iac: file=%s iac_type=%s findings=%d risk=%s",
        p.name, iac_type,
        len(parsed.get("security_compliance", [])),
        parsed.get("risk_score", "?"),
    )

    return {"review_id": review_id, "iac_type": iac_type, **parsed}
