"""Multi-Source Synthesis — merge N text inputs into one unified processified workflow."""
from __future__ import annotations


def synthesize_workflows(texts: list[str], llm_router=None) -> dict:
    """
    Merge N source texts into one deduplicated, unified processified workflow.

    If llm_router is None, falls back to a simple union merge without LLM.
    """
    if llm_router:
        return _llm_synthesize(texts, llm_router)
    return _naive_merge(texts)


def _naive_merge(texts: list[str]) -> dict:
    """Fallback: extract steps from each text naively, deduplicate by title."""
    import re
    seen: dict[str, dict] = {}
    for i, text in enumerate(texts):
        # Look for numbered or bulleted lines as step candidates
        for line in text.splitlines():
            m = re.match(r"^\s*(?:\d+[\.\)]\s*|[-*]\s+)(.*)", line.strip())
            if m:
                title = m.group(1).strip()
                if len(title) < 10 or len(title) > 200:
                    continue
                key = re.sub(r"\s+", " ", title.lower())[:80]
                if key not in seen:
                    seen[key] = {
                        "title": title,
                        "assignee_role": "Unassigned",
                        "checklist": [],
                    }
    return {
        "workflow_name": "Synthesized Workflow",
        "description": f"Merged from {len(texts)} source document(s)",
        "steps": list(seen.values()),
        "synthesis_method": "naive_merge",
    }


def _llm_synthesize(texts: list[str], llm_router) -> dict:
    import json
    from tools.llm.router import LLMRequest

    combined = "\n\n---\n\n".join(
        f"[Source {i + 1}]\n{t[:3000]}" for i, t in enumerate(texts)
    )
    system = (
        "You are a process analyst. The user will provide N source documents. "
        "Extract ALL distinct process steps across them, deduplicate near-duplicates, "
        "and return a single unified processified workflow as JSON with keys: "
        "workflow_name, description, steps (list of {title, assignee_role, checklist}). "
        "Return ONLY valid JSON."
    )
    prompt = (
        f"Synthesize the following {len(texts)} documents into one unified workflow:\n\n"
        + combined
    )
    try:
        result = llm_router.invoke(
            "multi_source_synthesis",
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=2048,
            ),
        )
        raw = result.content if hasattr(result, "content") else str(result)
        # Strip markdown fences if present
        import re
        raw = re.sub(r"^```(?:json)?\n?", "", raw.strip())
        raw = re.sub(r"\n?```$", "", raw)
        wf = json.loads(raw)
        wf["synthesis_method"] = "llm"
        return wf
    except Exception as exc:
        fallback = _naive_merge(texts)
        fallback["synthesis_error"] = str(exc)
        fallback["synthesis_method"] = "naive_merge_fallback"
        return fallback
