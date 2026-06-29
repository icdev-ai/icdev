# CUI // SP-CTI
"""IDR Firewall Config Review — Palo Alto, Cisco ASA, Fortinet, pfSense analysis.

Called by IDR Stage 2 for 'config' type uploads in the security domain profile.
Public entry point: review_firewall_config(file_path) -> dict
"""
from __future__ import annotations

import pathlib
import re
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.security.firewall_config_review")


def _detect_firewall_vendor(text: str, filename: str) -> str:
    """Return best-guess firewall vendor from text content and filename."""
    fname_lower = filename.lower()
    if "asa" in fname_lower or re.search(r"^(ASA|access-list|object-group|nat\s+\()", text, re.MULTILINE):
        return "cisco_asa"
    if re.search(r"^set (system|interface|policy|address|service)", text, re.MULTILINE):
        return "palo_alto" if "palo" in fname_lower else "fortinet"
    if re.search(r"^config (firewall|system|router)", text, re.MULTILINE):
        return "fortinet"
    if re.search(r"(pfsense|pfSense|<pfsense>)", text, re.IGNORECASE):
        return "pfsense"
    if re.search(r"^(hostname|interface|firewall-policy|access-list)", text, re.MULTILINE):
        return "cisco_asa"
    return "unknown_firewall"


_REVIEW_PROMPT = """\
You are a senior network security engineer specializing in firewall configuration review \
for government/defense environments (DISA STIG, NIST 800-53, FedRAMP/CMMC compliance required).

Device: {vendor} firewall
File: {filename}

INSTRUCTIONS:
1. Analyze the firewall configuration for security gaps, policy violations, overly permissive rules, \
missing controls, and compliance failures.
2. Pay special attention to: default deny policies, any-any rules, management plane exposure, \
VPN configuration, logging, high availability, and zone segmentation.
3. Return a single JSON object with this exact schema:

{{
  "security_compliance": [
    {{
      "title": "string",
      "severity": "CAT1|CAT2|CAT3|info",
      "detail": "string",
      "remediation": "string",
      "sample_config_snippet": "string (corrected config if applicable, else \"\")",
      "references": ["STIG-FW-V1-XX", "NIST-SC-7"]
    }}
  ],
  "optimization": [
    {{
      "title": "string",
      "detail": "string",
      "recommendation": "string",
      "sample_config_snippet": "string"
    }}
  ],
  "remediation": [
    {{
      "title": "string",
      "priority": "high|medium|low",
      "steps": ["step 1", "step 2"],
      "sample_config_snippet": "string",
      "verification": "string"
    }}
  ],
  "sample_template": "string — sanitized baseline firewall config skeleton (≤40 lines)",
  "explanation": "string — concise review summary (≤150 words)",
  "topology_graph": {{
    "nodes": [{{"id": "string", "label": "string", "type": "firewall|host|zone", "x": 0, "y": 0, "properties": {{}}}}],
    "edges": [{{"id": "string", "source": "string", "target": "string", "label": "string", "properties": {{}}}}]
  }}
}}

4. Severity: CAT1 = any-any permit/critical exposure/default allow; \
CAT2 = significant weakness; CAT3 = minor hardening gap; info = observation.
5. Limit each array to top 5 items.

Firewall configuration ({vendor}):
```
{config_content}
```
"""


def _call_llm(prompt: str) -> str:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2500,
            temperature=0.1,
            skip_injection_scan=True,
        )
        resp = router.invoke("ndc_config_review", req)
        return getattr(resp, "content", "") or ""
    except Exception as exc:
        logger.warning("firewall_config_review LLM call failed: %s", exc)
        return ""


def _parse_response(raw: str, vendor: str) -> dict[str, Any]:
    """Parse LLM JSON response into canonical review dict."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    blob = re.search(r"\{.*\}", text, re.DOTALL)
    candidate = (fence.group(1) if fence else None) or (blob.group() if blob else None)
    if not candidate:
        return _fallback(vendor, "LLM returned no JSON")

    import json

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return _fallback(vendor, f"JSON parse error: {exc}")

    def _as_list(v: Any) -> list:
        if isinstance(v, list):
            return [dict(i) if isinstance(i, dict) else {"title": str(i)} for i in v]
        if isinstance(v, dict):
            return [dict(v)]
        return []

    def _normalize_topo(v: Any) -> dict:
        if isinstance(v, dict):
            return {"nodes": _as_list(v.get("nodes")), "edges": _as_list(v.get("edges"))}
        return {"nodes": [], "edges": []}

    return {
        "security_compliance": _as_list(data.get("security_compliance")),
        "optimization": _as_list(data.get("optimization")),
        "remediation": _as_list(data.get("remediation")),
        "sample_template": str(data.get("sample_template") or ""),
        "explanation": str(data.get("explanation") or ""),
        "topology_graph": _normalize_topo(data.get("topology_graph")),
    }


def _fallback(vendor: str, reason: str) -> dict[str, Any]:
    return {
        "security_compliance": [{
            "title": "Firewall review generated with deterministic fallback",
            "severity": "info",
            "detail": reason,
            "remediation": "Re-run when LLM provider is available.",
            "sample_config_snippet": "",
            "references": [],
        }],
        "optimization": [],
        "remediation": [],
        "sample_template": f"! {vendor} baseline template — manual review required",
        "explanation": f"Automated {vendor} firewall review unavailable. Manual review required.",
        "topology_graph": {"nodes": [], "edges": []},
    }


# ── Public Entry Point ────────────────────────────────────────────────────────

def review_firewall_config(file_path: str) -> dict[str, Any]:
    """Read a firewall config file and run a full LLM-based security review.

    Called by IDR Stage 2 for 'config' type uploads (security domain profile).
    Returns: {review_id, vendor, security_compliance, optimization, remediation,
               sample_template, explanation, topology_graph}
    """
    review_id = str(uuid.uuid4())
    p = pathlib.Path(file_path)

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("review_firewall_config: cannot read %s: %s", file_path, exc)
        result = _fallback("unknown", f"Cannot read file: {exc}")
        result["review_id"] = review_id
        result["vendor"] = "unknown"
        return result

    vendor = _detect_firewall_vendor(content, p.name)
    prompt = _REVIEW_PROMPT.format(
        vendor=vendor,
        filename=p.name,
        config_content=content[:20_000],
    )

    raw = _call_llm(prompt)
    parsed = _parse_response(raw, vendor)

    logger.info(
        "review_firewall_config: file=%s vendor=%s findings=%d",
        p.name, vendor,
        len(parsed.get("security_compliance", [])),
    )

    return {"review_id": review_id, "vendor": vendor, **parsed}
