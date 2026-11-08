# CUI // SP-CTI
"""Process-Ify: turn a process document into a structured workflow draft.

The logic the dashboard's ``POST /workflow-canvas/api/workflows/processify`` ran
inline, lifted out so the route and the CLI share one implementation (icx-cli-01).
The model call goes through the governed Cortex facade under the
``process_digitization`` function; the document text is untrusted, so the
injection screen runs (``trusted_content`` stays False).

CLI (JSON out; nothing is saved and no kanban card is seeded)::

    python -m tools.workflow_canvas.processify --from-doc sop.docx [--industry Logistics] --json
    python -m tools.workflow_canvas.processify --observed capture.json --json

``--observed`` reads a captured scenario (``observed_processify.capture_scenario``
output) and returns the deterministic draft built from what was observed, with
no model call.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Callable

from icdev.core.paths import repo_root  # noqa: E402

_BASE = repo_root(__file__)
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.logging.icdev_logger import get_logger  # noqa: E402
from tools.workflow_canvas.constants import MAX_EXTRACT_CHARS  # noqa: E402

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt"}
VALID_FIELD_TYPES = {"text", "textarea", "number", "date", "select", "multiselect", "checkbox", "email", "file",
                     "richtext"}
ACE_ROLE_HINTS = [
    ("review", "doc_reviewer"), ("document", "document_reviewer"),
    ("compliance", "compliance_manager"), ("security", "security_analyst"),
    ("test", "qa_manager"), ("quality", "qa_manager"),
    ("requirement", "requirements_engineer"), ("submit", "document_contributor"),
    ("approve", "compliance_manager"), ("verify", "adversarial_verifier"),
    ("deploy", "devops_engineer"), ("analyze", "data_analyst"),
    ("research", "researcher"), ("write", "technical_writer"),
    ("report", "technical_writer"),
]


def validate_fields(raw_fields: list) -> list:
    """Coerce and validate a list of raw field dicts from LLM output."""
    cleaned = []
    for i, f in enumerate(raw_fields):
        if not isinstance(f, dict):
            continue
        cleaned.append({
            "id": str(f.get("id") or f"f{i+1}"),
            "type": str(f.get("type", "text")) if f.get("type") in VALID_FIELD_TYPES else "text",
            "label": str(f.get("label") or f"Field {i+1}"),
            "required": bool(f.get("required", False)),
            "placeholder": str(f.get("placeholder", "")) if f.get("placeholder") else "",
            "options": [str(o) for o in f["options"]] if f.get("options") and isinstance(f["options"], list) else None,
        })
    return cleaned


def suggest_ace_role(title: str, description: str) -> str:
    text = (title + " " + description).lower()
    for keyword, role in ACE_ROLE_HINTS:
        if keyword in text:
            return role
    return "doc_reviewer"


def extract_text(path: str | pathlib.Path) -> str:
    """Text of a process document: the document-intelligence extractors, then markitdown."""
    path = pathlib.Path(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported format '{path.suffix.lower()}'. Use: PDF, DOCX, PNG, JPG, or TXT.")
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()
    text = ""
    try:
        from tools.document_intelligence.extractors import extract_file
        text = (extract_file(str(path)).text or "").strip()
    except Exception as e1:  # noqa: BLE001 -- fall through to the next converter, as the route did
        logger.warning("digitize: extractors failed (%s)", e1)
    if not text:
        try:
            from tools.document_intelligence.converters.markitdown_adapter import convert as md_convert
            text = (md_convert(path).text or "").strip()
        except Exception as e2:  # noqa: BLE001
            logger.warning("digitize: markitdown failed (%s)", e2)
    return text


def build_prompt(text: str, industry: str, source_name: str) -> str:
    return f"""You are a business process analyst. Analyze the following process document and convert it into a structured digital workflow.

Industry: {industry}
Document: {source_name}

Return a JSON object with this exact structure:
{{
  "workflow_name": "concise workflow name",
  "workflow_description": "one sentence describing the process",
  "industry": "{industry}",
  "estimated_duration_days": 30,
  "steps": [
    {{
      "id": "step-1",
      "title": "Step title",
      "phase": "Phase name (Initiation|Review|Approval|Completion etc.)",
      "description": "What happens in this step",
      "checklist_items": ["Item from document 1", "Item from document 2"],
      "form_fields": [
        {{"label": "Field label", "type": "text", "required": true, "options": null}}
      ],
      "assignee_role": "Who performs this step",
      "reviewer_role": "Who reviews (or empty string)",
      "approver_role": "Who approves (or empty string)",
      "sla_days": 5,
      "dependencies": [],
      "ace_role": "snake_case_ai_coworker_role_specific_to_this_step"
    }}
  ]
}}

Rules:
- Extract ALL steps/tasks/requirements found in the document
- checklist_items should be concrete, verbatim items from the document
- form_fields: REQUIRED — provide 2-5 meaningful data fields that the assignee must fill in to complete this step. Never leave form_fields empty.
- assignee_role/reviewer_role/approver_role are human job titles (e.g. "Provider", "Network Director")
- dependencies lists step ids that must complete first (first step has empty [])
- Aim for 5-15 steps covering the full process
- field type must be one of: text textarea number date select multiselect checkbox email file
- ace_role: suggest a specific, domain-appropriate AI coworker role in snake_case (e.g. credentialing_specialist, dhcs_compliance_auditor, loan_underwriter, permit_reviewer, safety_inspector) — be specific to the industry, not generic
- Return ONLY valid JSON. No markdown fences, no explanation.

Process Document:
{text[:8000]}"""


def _cortex_complete(prompt: str) -> str:
    from tools.cortex import api as cortex_api
    from tools.cortex.schemas import CortexContext
    cx = cortex_api.complete(
        prompt,
        function="process_digitization",
        ctx=CortexContext(domain="workflow", agent_id="wfc-processify", trusted_content=False),
        max_tokens=4096,
        temperature=0.2,
    )
    return (cx.text or "") if cx is not None else ""


def digitize(text: str, *, industry: str = "General", source_name: str = "Process Document",
             complete: Callable[[str], str] | None = None) -> dict:
    """The workflow draft for a process document's text. ``complete`` is the model call (default: Cortex)."""
    if not text:
        raise ValueError("Could not extract text. Try a clearer document or paste text directly.")
    if len(text) > MAX_EXTRACT_CHARS:
        logger.info("digitize: truncating extracted text %d -> %d chars", len(text), MAX_EXTRACT_CHARS)
        text = text[:MAX_EXTRACT_CHARS]
    raw = (complete or _cortex_complete)(build_prompt(text, industry, source_name))
    match = re.search(r'\{[\s\S]*\}', raw)
    if not match:
        raise ValueError(f"LLM did not return valid JSON. Raw: {raw[:300]}")
    workflow_def = json.loads(match.group())
    steps = workflow_def.get("steps", [])
    for i, step in enumerate(steps):
        if "id" not in step:
            step["id"] = f"step-{i + 1}"
        # Use the model's suggested role first; the keyword-hint map is the fallback
        if not step.get("ace_role"):
            step["ace_role"] = suggest_ace_role(step.get("title", ""), step.get("description", ""))
        step["form_fields"] = validate_fields(step.get("form_fields") or [])
        step.setdefault("checklist_items", [])
        step.setdefault("dependencies", [])
    workflow_def["steps"] = steps
    workflow_def["source_name"] = source_name
    workflow_def["basis"] = "document"
    workflow_def["label"] = "modelled"
    return workflow_def


def from_observed(capture: dict) -> dict:
    """The deterministic draft from a captured scenario (no model call); ``steps`` is None when nothing was measured."""
    from tools.workflow_canvas import observed_processify
    return observed_processify.build_draft(capture)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Process-Ify: a process document (or an observed capture) as a workflow draft")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-doc", type=pathlib.Path, help="a PDF, DOCX, PNG, JPG or TXT process document")
    src.add_argument("--observed", type=pathlib.Path, help="a captured scenario (JSON) from observed_processify")
    ap.add_argument("--industry", default="General")
    ap.add_argument("--json", action="store_true", help="JSON out (the default; kept for CLI parity)")
    a = ap.parse_args(argv)
    try:
        if a.from_doc:
            out = {"status": "ok", "workflow": digitize(extract_text(a.from_doc), industry=a.industry,
                                                        source_name=a.from_doc.stem)}
        else:
            out = {"status": "ok", "workflow": from_observed(json.loads(a.observed.read_text(encoding="utf-8")))}
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
