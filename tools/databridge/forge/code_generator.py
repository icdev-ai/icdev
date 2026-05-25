# CUI // SP-CTI
"""Code Generator — Stage 3 of Connector Forge pipeline (D-CF-1).

Two-tier LLM: qwen3 drafts connector body, Claude reviews and merges.
Falls back to Jinja2-only template if LLM unavailable (air-gap safe).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tools.databridge.forge.base_selector import BaseClassSelection
from tools.databridge.forge.spec_parser import ForgeApiManifest
from tools.databridge.forge.templates import TEMPLATE_MAP

logger = get_logger("databridge.forge.code_generator")


def generate_connector_code(
    manifest: ForgeApiManifest,
    selection: BaseClassSelection,
    connector_name: str,
    display_name: str = "",
    use_llm: bool = True,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate connector source code.

    Returns:
        {
            "code": str,       # final Python source
            "hash": str,       # sha256 of code
            "method": str,     # "llm_two_tier" | "template_only"
            "template": str,   # template name used
            "warnings": list,
        }
    """
    # Step 1: Render Jinja2 skeleton
    skeleton = _render_skeleton(manifest, selection, connector_name, display_name)

    if not use_llm:
        return _finalize(skeleton, "template_only", selection.template_name)

    # Step 2: Two-tier LLM
    try:
        final_code = _two_tier_generate(skeleton, manifest, connector_name)
        if final_code:
            return _finalize(final_code, "llm_two_tier", selection.template_name)
    except Exception as exc:
        logger.warning("LLM generation failed, using template only: %s", exc)

    return _finalize(skeleton, "template_only", selection.template_name)


def _render_skeleton(
    manifest: ForgeApiManifest,
    selection: BaseClassSelection,
    connector_name: str,
    display_name: str,
) -> str:
    """Render connector skeleton from Jinja2 template with fallback."""
    template_str = TEMPLATE_MAP.get(selection.template_name, TEMPLATE_MAP["rest_connector"])
    class_name = _to_class_name(connector_name)
    endpoint_map = {ep.path.split("/")[-1] or ep.path: ep.path for ep in manifest.endpoints[:20]}
    table_names = list(endpoint_map.keys()) or ["default"]
    has_write = any(e.method in ("POST", "PUT", "PATCH") for e in manifest.endpoints)

    context = {
        "connector_name": connector_name,
        "class_name": class_name,
        "display_name": display_name or manifest.title or connector_name,
        "base_url": manifest.base_url,
        "auth_method": manifest.auth_methods[0] if manifest.auth_methods else "api_key",
        "supports_read": "True",
        "supports_write": str(has_write),
        "supports_incremental": "False",
        "max_batch_size": "10000",
        "endpoint_map": repr(endpoint_map),
        "table_names": repr(table_names),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Try Jinja2 first
    try:
        from jinja2 import Template

        return _ensure_trailing_newline(Template(template_str).render(**context))
    except ImportError:
        pass

    # String replacement fallback (air-gap safe)
    result = template_str
    for key, value in context.items():
        result = result.replace("{{ " + key + " }}", str(value))
        result = result.replace("{{" + key + "}}", str(value))
    return _ensure_trailing_newline(result)


def _two_tier_generate(
    skeleton: str,
    manifest: ForgeApiManifest,
    connector_name: str,
) -> Optional[str]:
    """Run two-tier LLM: qwen3 draft → Claude review."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
    except ImportError:
        raise RuntimeError("LLM router not available")

    summary = _manifest_summary(manifest)

    # qwen3 worker prompt (compact, ~400 words output)
    worker_prompt = (
        f"You are generating a Python DataBridge connector for '{connector_name}'.\n"
        f"API manifest: {summary}\n\n"
        f"Here is the skeleton to complete:\n```python\n{skeleton}\n```\n\n"
        "Fill in the TODO sections only. Output the complete connector code. "
        "Keep output under 400 words. Use only stdlib and the already-imported libraries. "
        "Ensure config['_resolved_secret'] is used for authentication."
    )

    # Claude reviewer prompt
    reviewer_prompt = (
        f"Review this generated DataBridge connector for '{connector_name}'.\n"
        "Check:\n"
        "1) All abstract methods from the base class are implemented\n"
        "2) No unsafe imports (os.system, subprocess, eval, exec)\n"
        "3) No hardcoded secrets or credentials\n"
        "4) Authentication uses config['_resolved_secret'] pattern\n"
        "5) ConnectorResponse fields are correct\n"
        "Return the corrected, complete Python source code only."
    )

    try:
        worker_request = LLMRequest(
            messages=[{"role": "user", "content": worker_prompt}],
        )
        worker_resp = router.invoke("connector_code_generation", worker_request)
        worker_draft = worker_resp.content if worker_resp and worker_resp.content else ""

        review_request = LLMRequest(
            messages=[
                {"role": "user", "content": f"{reviewer_prompt}\n\nCode to review:\n```python\n{worker_draft}\n```"}
            ],
        )
        review_resp = router.invoke("connector_code_generation", review_request)
        final = review_resp.content if review_resp and review_resp.content else ""
        return _extract_code_block(final) or _extract_code_block(worker_draft)
    except Exception as exc:
        raise RuntimeError(f"Two-tier generation failed: {exc}") from exc


def _extract_code_block(text: str) -> Optional[str]:
    """Extract Python code block from LLM output."""
    match = re.search(r"```python\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # If no code fence, check if it looks like Python
    if "class " in text and "def " in text:
        return text.strip()
    return None


def _manifest_summary(manifest: ForgeApiManifest) -> str:
    """Compact summary for LLM context."""
    return (
        f"title={manifest.title}, base_url={manifest.base_url}, "
        f"protocol={manifest.protocol}, auth={manifest.auth_methods}, "
        f"endpoints={[e.path for e in manifest.endpoints[:5]]}"
    )


def _to_class_name(name: str) -> str:
    """Convert connector name to PascalCase class name."""
    return "".join(w.capitalize() for w in re.split(r"[_\-\s]+", name)) + "Connector"


def _ensure_trailing_newline(code: str) -> str:
    """Ensure code ends with exactly one newline (ruff W292)."""
    return code.rstrip() + "\n"


def _finalize(code: str, method: str, template: str) -> Dict[str, Any]:
    """Wrap generated code with metadata."""
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return {
        "code": code,
        "hash": code_hash,
        "method": method,
        "template": template,
        "warnings": [],
    }
