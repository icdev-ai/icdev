"""Multi-Source Workflow Synthesizer — combine N documents into one deduplicated workflow."""
from __future__ import annotations
import json


def synthesize_workflows(workflow_dicts: list[dict]) -> dict:
    """
    Given a list of processified workflow dicts, synthesize a single unified workflow
    via LLM with deduplication and conflict resolution.
    """
    from tools.llm.router import LLMRouter
    from tools.llm.provider import LLMRequest

    if not workflow_dicts:
        raise ValueError("At least one workflow required for synthesis")

    if len(workflow_dicts) == 1:
        return workflow_dicts[0]

    # Build a condensed representation of each workflow for the prompt
    summaries = []
    for i, wf in enumerate(workflow_dicts):
        steps = wf.get("steps") or []
        name = wf.get("workflow_name") or wf.get("name") or f"Workflow {i+1}"
        step_lines = "\n".join(
            f"  {j+1}. {s.get('title','')} [{s.get('assignee_role','')}]"
            for j, s in enumerate(steps)
        )
        summaries.append(f"--- Source {i+1}: {name} ({len(steps)} steps) ---\n{step_lines}")

    prompt = (
        "You are synthesizing multiple process workflow definitions into a single unified workflow.\n\n"
        "Source workflows:\n\n"
        + "\n\n".join(summaries)
        + "\n\n"
        "Instructions:\n"
        "- Merge all steps into a single logical sequence\n"
        "- Deduplicate steps that represent the same activity (use the most complete version)\n"
        "- Resolve role conflicts by choosing the most appropriate role\n"
        "- Preserve all unique steps that appear in any source\n"
        "- Order steps in the most logical execution sequence\n\n"
        "Return ONLY valid JSON matching this schema exactly:\n"
        '{"workflow_name": "...", "industry": "...", "description": "...", "steps": ['
        '{"title": "...", "assignee_role": "...", "reviewer_role": "...", '
        '"approver_role": "...", "checklist": ["..."], "sla_hours": null}]}'
    )

    try:
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="You are a process architect. Return only valid JSON.",
            max_tokens=3000,
        )
        result = router.invoke("workflow_synthesis", req)
        raw = (result.content if hasattr(result, "content") else str(result)).strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1][4:].strip() if parts[1].startswith("json") else parts[1].strip()
        synthesized = json.loads(raw)
        synthesized["_source_count"] = len(workflow_dicts)
        return synthesized
    except Exception as exc:
        # Fallback: simple concatenation + dedup by title
        all_steps = []
        seen_titles: set[str] = set()
        for wf in workflow_dicts:
            for step in (wf.get("steps") or []):
                key = (step.get("title") or "").lower().strip()
                if key and key not in seen_titles:
                    seen_titles.add(key)
                    all_steps.append(step)

        first_wf = workflow_dicts[0]
        return {
            "workflow_name": "Synthesized Workflow (fallback)",
            "industry": first_wf.get("industry", ""),
            "description": f"Merged from {len(workflow_dicts)} sources (LLM unavailable: {exc})",
            "steps": all_steps,
            "_source_count": len(workflow_dicts),
            "_synthesis_fallback": True,
        }
