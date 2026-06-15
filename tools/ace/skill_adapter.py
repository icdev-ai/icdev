# CUI // SP-CTI
"""SkillHub — Generic Multi-Framework Skill Adapter.

Auto-detects and normalizes skills from any AI framework into a common
ICDEV skill schema that the SkillHub can import, display, and install.

Supported frameworks
--------------------
claude      .claude/commands/*.md  — Claude Code slash commands
hermes      Nous Hermes agent YAML/JSON — tools, system_prompt, name
gemini      Google Gemini functionDeclarations JSON
openai      OpenAI function_call / tool spec JSON
langchain   LangChain tool definition (name/description/args_schema)
crewai      CrewAI agent YAML (role/goal/backstory/tools)
autogen     AutoGen agent JSON (name/system_message/description)
generic     Any text blob — name + description extracted heuristically

Normalized ICDEV Skill Schema
------------------------------
{
  "id": str,                # slug
  "name": str,              # display name
  "description": str,       # what it does
  "framework": str,         # source framework label
  "version": str,
  "steps": [str],           # actions / capabilities
  "system_prompt": str,     # injected as context (if applicable)
  "tools": [str],           # tool names the skill uses
  "parameters": {str: str}, # input parameter names → descriptions
  "install_path": str,      # where ICDEV installs it
  "raw": str,               # original content
  "confidence": float,      # detection confidence 0.0–1.0
}

Usage
-----
    from icdev.tools.ace.skill_adapter import detect_and_normalize
    skill = detect_and_normalize(raw_content, filename="my_skill.yaml")
    print(skill["framework"], skill["name"])
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "detect_and_normalize",
    "detect_framework",
    "SUPPORTED_FRAMEWORKS",
    "SkillAdapterError",
]

SUPPORTED_FRAMEWORKS = [
    "claude", "hermes", "gemini", "openai", "langchain", "crewai", "autogen", "generic"
]


class SkillAdapterError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_and_normalize(raw: str, filename: str = "") -> dict[str, Any]:
    """Detect framework and return a normalized ICDEV skill dict."""
    framework, confidence = detect_framework(raw, filename)
    normalizer = _NORMALIZERS.get(framework, _normalize_generic)
    skill = normalizer(raw, filename)
    skill["framework"] = framework
    skill["confidence"] = confidence
    skill["raw"] = raw
    skill.setdefault("id", _slugify(skill.get("name", filename or "unknown")))
    skill.setdefault("version", "1.0")
    skill.setdefault("steps", [])
    skill.setdefault("system_prompt", "")
    skill.setdefault("tools", [])
    skill.setdefault("parameters", {})
    skill.setdefault("install_path", _default_install_path(framework, skill["id"]))
    return skill


def detect_framework(raw: str, filename: str = "") -> tuple[str, float]:
    """Return (framework_name, confidence_0_to_1)."""
    ext = Path(filename).suffix.lower()
    raw_lower = raw.lower()

    # Claude Code slash command: .md with ## or # heading + imperative instructions
    if ext == ".md" or filename.endswith(".md"):
        if re.search(r"^#\s+\S", raw, re.MULTILINE):
            if any(kw in raw_lower for kw in ("claude", "slash command", "$arguments", "## instructions", "## usage")):
                return "claude", 0.95
            return "claude", 0.75

    # Hermes (Nous Research) agent: YAML/JSON with tools list + system_prompt
    if _try_json(raw):
        d = _try_json(raw)
        if d and isinstance(d, dict):
            if "system_prompt" in d and "tools" in d:
                return "hermes", 0.95
            if "functionDeclarations" in d or "function_declarations" in d:
                return "gemini", 0.95
            if "function" in d and "parameters" in d.get("function", {}):
                return "openai", 0.9
            if "functions" in d and isinstance(d.get("functions"), list):
                funcs = d["functions"]
                if funcs and "parameters" in (funcs[0] or {}):
                    return "openai", 0.85
            if "name" in d and "description" in d and "tools" in d:
                return "langchain", 0.8
    if ext in (".yaml", ".yml"):
        import yaml as _yaml
        try:
            d = _yaml.safe_load(raw)
            if isinstance(d, dict):
                if "system_prompt" in d and "tools" in d:
                    return "hermes", 0.92
                if "role" in d and "goal" in d and "backstory" in d:
                    return "crewai", 0.95
                if "name" in d and "system_message" in d:
                    return "autogen", 0.9
                if "name" in d and "description" in d:
                    return "langchain", 0.7
        except Exception:
            pass

    # CrewAI: role/goal/backstory keys
    if all(k in raw_lower for k in ("role:", "goal:", "backstory:")):
        return "crewai", 0.85
    # AutoGen: system_message key
    if "system_message" in raw_lower and ("agent" in raw_lower or "name" in raw_lower):
        return "autogen", 0.8
    # Gemini: functionDeclarations pattern
    if "functiondeclarations" in raw_lower or "function_declarations" in raw_lower:
        return "gemini", 0.85
    # OpenAI function spec
    if '"type": "function"' in raw or "'type': 'function'" in raw:
        return "openai", 0.8

    return "generic", 0.5


# ---------------------------------------------------------------------------
# Per-framework normalizers
# ---------------------------------------------------------------------------


def _normalize_claude(raw: str, filename: str) -> dict[str, Any]:
    """Parse Claude Code slash command .md."""
    name = ""
    description = ""
    steps: list[str] = []
    system_prompt = ""

    # Name from first H1
    m = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    if m:
        name = m.group(1).strip()
    elif filename:
        name = Path(filename).stem.replace("-", " ").replace("_", " ").title()

    # Description from first paragraph after heading
    m2 = re.search(r"^#[^#].*?\n+(.+?)(?:\n\n|\Z)", raw, re.DOTALL)
    if m2:
        candidate = m2.group(1).strip()
        if not candidate.startswith("#"):
            description = candidate[:300]

    # Steps from ## sections or bullet list
    sections = re.findall(r"^#{2,3}\s+(.+)$", raw, re.MULTILINE)
    steps = [s.strip() for s in sections if s.strip()]

    # System prompt = everything after ## Instructions or ## System
    m3 = re.search(r"#{1,3}\s+(?:instructions?|system prompt)\s*\n+(.+?)(?:\n#{1,3}|\Z)", raw, re.IGNORECASE | re.DOTALL)
    if m3:
        system_prompt = m3.group(1).strip()[:1000]

    # Tools from $ARGUMENTS or mentioned tools
    tools = re.findall(r"`([\w_]+(?:\.py|_tool)?)`", raw)

    return {
        "name": name or Path(filename).stem,
        "description": description,
        "steps": steps,
        "system_prompt": system_prompt,
        "tools": list(dict.fromkeys(tools)),
        "parameters": {"$ARGUMENTS": "positional arguments to the command"},
    }


def _normalize_hermes(raw: str, filename: str) -> dict[str, Any]:
    """Parse Nous Hermes agent JSON/YAML."""
    d = _load_dict(raw)
    name = d.get("name") or d.get("agent_name") or Path(filename).stem
    return {
        "name": str(name).title(),
        "description": str(d.get("description") or d.get("agent_description") or ""),
        "system_prompt": str(d.get("system_prompt") or ""),
        "tools": [_tool_name(t) for t in (d.get("tools") or [])],
        "steps": list(d.get("steps") or []),
        "parameters": _extract_params(d.get("parameters") or {}),
    }


def _normalize_gemini(raw: str, filename: str) -> dict[str, Any]:
    """Parse Google Gemini functionDeclarations."""
    d = _load_dict(raw)
    decls = d.get("functionDeclarations") or d.get("function_declarations") or []
    if not decls and isinstance(d, list):
        decls = d
    if not decls:
        return _normalize_generic(raw, filename)
    first = decls[0] if decls else {}
    name = first.get("name") or Path(filename).stem
    params = {}
    props = (first.get("parameters") or {}).get("properties") or {}
    for k, v in props.items():
        params[k] = v.get("description", "")
    return {
        "name": str(name),
        "description": str(first.get("description") or ""),
        "steps": [f.get("name", "") for f in decls if f.get("name")],
        "system_prompt": "",
        "tools": [f.get("name", "") for f in decls],
        "parameters": params,
    }


def _normalize_openai(raw: str, filename: str) -> dict[str, Any]:
    """Parse OpenAI function_call / tool spec."""
    d = _load_dict(raw)
    # Handle {"functions": [...]} or {"tools": [{"type":"function","function":{}}]} or direct
    funcs = []
    if "functions" in d:
        funcs = d["functions"]
    elif "tools" in d:
        funcs = [t.get("function", t) for t in d["tools"] if isinstance(t, dict)]
    elif "function" in d:
        funcs = [d["function"]]
    elif "name" in d:
        funcs = [d]
    first = funcs[0] if funcs else {}
    params = {}
    props = (first.get("parameters") or {}).get("properties") or {}
    for k, v in props.items():
        params[k] = v.get("description", "") if isinstance(v, dict) else str(v)
    return {
        "name": str(first.get("name") or Path(filename).stem),
        "description": str(first.get("description") or ""),
        "steps": [f.get("name", "") for f in funcs if f.get("name")],
        "system_prompt": "",
        "tools": [f.get("name", "") for f in funcs],
        "parameters": params,
    }


def _normalize_langchain(raw: str, filename: str) -> dict[str, Any]:
    """Parse LangChain tool definition."""
    d = _load_dict(raw)
    return {
        "name": str(d.get("name") or Path(filename).stem),
        "description": str(d.get("description") or ""),
        "steps": [],
        "system_prompt": "",
        "tools": [str(d.get("name", ""))],
        "parameters": _extract_params(d.get("args_schema") or d.get("parameters") or {}),
    }


def _normalize_crewai(raw: str, filename: str) -> dict[str, Any]:
    """Parse CrewAI agent definition."""
    d = _load_dict(raw)
    name = d.get("role") or d.get("name") or Path(filename).stem
    system_prompt = f"Role: {d.get('role', '')}\nGoal: {d.get('goal', '')}\nBackstory: {d.get('backstory', '')}"
    return {
        "name": str(name),
        "description": str(d.get("goal") or ""),
        "steps": list(d.get("tasks") or []),
        "system_prompt": system_prompt.strip(),
        "tools": [_tool_name(t) for t in (d.get("tools") or [])],
        "parameters": {},
    }


def _normalize_autogen(raw: str, filename: str) -> dict[str, Any]:
    """Parse AutoGen agent definition."""
    d = _load_dict(raw)
    return {
        "name": str(d.get("name") or Path(filename).stem),
        "description": str(d.get("description") or ""),
        "steps": [],
        "system_prompt": str(d.get("system_message") or ""),
        "tools": [],
        "parameters": _extract_params(d.get("llm_config") or {}),
    }


def _normalize_generic(raw: str, filename: str) -> dict[str, Any]:
    """Heuristic extraction for unrecognized formats."""
    name = Path(filename).stem.replace("-", " ").replace("_", " ").title() if filename else "Imported Skill"
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    description = lines[0][:300] if lines else ""
    # Look for a name-like first line
    if lines and len(lines[0]) < 80 and not lines[0].startswith("{"):
        name = lines[0].lstrip("#").strip()
        description = " ".join(lines[1:4])[:300] if len(lines) > 1 else ""
    return {
        "name": name,
        "description": description,
        "steps": [],
        "system_prompt": raw[:1000],
        "tools": [],
        "parameters": {},
    }


_NORMALIZERS = {
    "claude": _normalize_claude,
    "hermes": _normalize_hermes,
    "gemini": _normalize_gemini,
    "openai": _normalize_openai,
    "langchain": _normalize_langchain,
    "crewai": _normalize_crewai,
    "autogen": _normalize_autogen,
    "generic": _normalize_generic,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower().strip()).strip("_")


def _default_install_path(framework: str, skill_id: str) -> str:
    _PATHS = {
        "claude": f".claude/commands/{skill_id}.md",
        "hermes": f".agents/skills/{skill_id}.yaml",
        "gemini": f".agents/skills/gemini/{skill_id}.json",
        "openai": f".agents/skills/openai/{skill_id}.json",
        "langchain": f".agents/skills/langchain/{skill_id}.py",
        "crewai": f".agents/skills/crewai/{skill_id}.yaml",
        "autogen": f".agents/skills/autogen/{skill_id}.json",
    }
    return _PATHS.get(framework, f".agents/skills/{skill_id}.txt")


def _try_json(raw: str) -> dict | None:
    try:
        v = json.loads(raw.strip())
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _load_dict(raw: str) -> dict:
    d = _try_json(raw)
    if d:
        return d
    try:
        import yaml as _yaml
        v = _yaml.safe_load(raw)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {}


def _tool_name(t: Any) -> str:
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return t.get("name") or t.get("function") or str(t)
    return str(t)


def _extract_params(schema: Any) -> dict[str, str]:
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties") or schema
    return {
        k: (v.get("description", "") if isinstance(v, dict) else str(v))
        for k, v in props.items()
        if isinstance(k, str)
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse, sys  # noqa: E401

    parser = argparse.ArgumentParser(description="SkillHub multi-framework adapter")
    parser.add_argument("--file", help="Path to skill file to parse")
    parser.add_argument("--raw", help="Raw skill content (stdin if omitted)")
    parser.add_argument("--detect", action="store_true", help="Detect framework only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.file:
        content = Path(args.file).read_text(encoding="utf-8")
        fname = args.file
    elif args.raw:
        content = args.raw
        fname = "stdin.txt"
    else:
        content = sys.stdin.read()
        fname = "stdin.txt"

    if args.detect:
        fw, conf = detect_framework(content, fname)
        result = {"framework": fw, "confidence": conf}
    else:
        result = detect_and_normalize(content, fname)
        result.pop("raw", None)  # don't echo raw in CLI output

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Framework: {result.get('framework')} ({result.get('confidence', 0):.0%})")
        print(f"Name:      {result.get('name')}")
        print(f"Desc:      {result.get('description', '')[:80]}")
        print(f"Tools:     {result.get('tools')}")
        print(f"Steps:     {result.get('steps')}")
