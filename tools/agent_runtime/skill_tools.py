# CUI // SP-CTI
"""Progressive skill disclosure for agent runtimes (hgx-sess-02).

Skills were invisible to the model. The whole lifecycle — propose → HITL approve
→ write ``SKILL.md`` → curate (``tools/agent_runtime/skills_lifecycle.py``) —
produced artifacts only a human ever read: ``/skills`` prints names to the
operator's terminal, and no tool in any bundle, built-in toolset or ACE registry
let an agent reach them.

This module closes that with **two** read-only tools rather than one, which is
what makes the disclosure progressive:

- ``list_skills`` — name + one-line description for every indexed skill. Reads
  only ``tools/skills/registry.py``'s committed cache, never a ``SKILL.md``
  body, so its cost is a function of the skill *count* and is independent of how
  long any skill is. Cheap enough to be always available.
- ``load_skill``  — the full body of ONE named skill, on demand. The agent pays
  for a body only when it has decided, from the listing, that it wants that one.

Loading a body also calls :func:`skills_lifecycle.record_use`, which until now
had zero callers: ``use_count`` never incremented and ``last_activity_at`` was
only ever stamped at promotion time, so the curator's 30-day idle sweep graded
every promoted auto-skill on a field nothing wrote — making each one
archive-eligible 30 days after promotion no matter how heavily it was used. A
body load is the honest definition of "used": it is the point at which a skill's
instructions actually enter a model's context.

Both handlers match the :data:`icdev.tools.llm.agent_loop.ToolHandler` contract
``handler(input_dict, stop_event) -> str`` and return an ``error: ...`` string
rather than raising, so a missing registry, DB or skills tree degrades instead of
breaking the turn.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.skill_tools")

ToolHandler = Callable[[dict[str, Any], threading.Event | None], str]

#: Per-skill description budget in the listing. The registry's descriptions are
#: already one-liners; this bounds a pathological one.
_MAX_DESC_CHARS = 240

#: Whole-listing ceiling. With ~23 skills the listing lands near 4 KB, so this is
#: headroom, not a routine truncation — but it is what makes the "bounded number
#: of tokens" guarantee hold if the skills tree grows without limit.
_MAX_LIST_CHARS = 16_000

#: One skill body's ceiling. A SKILL.md is instructions, not data; anything past
#: this is truncated with a marker rather than silently cut.
_MAX_BODY_CHARS = 60_000

_SKILLS_SUBDIR = (".agents", "skills")


# ---------------------------------------------------------------------------
# Repo root — resolved from __file__, never cwd. This module is mirrored to both
# <repo>/tools/agent_runtime/ and <repo>/icdev/tools/agent_runtime/, which sit at
# different depths, so walk upward to a sentinel rather than counting parents.
# ---------------------------------------------------------------------------
def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


def _repo_root() -> Path:
    return _REPO_ROOT


# ---------------------------------------------------------------------------
# Registry access (reuses tools/skills/registry.py + its committed cache)
# ---------------------------------------------------------------------------
def _skill_entries() -> dict[str, dict[str, Any]]:
    """Return ``{skill_name: entry}`` from the committed registry cache.

    ``load_registry()`` serves ``tools/skills/registry.json`` and only re-parses
    ``.agents/skills/`` when the cache is absent or its schema version moved, so
    a turn-time call is a JSON read rather than a tree walk.
    """
    try:
        from tools.skills.registry import load_registry

        reg = load_registry()
    except Exception as exc:  # noqa: BLE001 — a broken registry must not break the turn
        logger.warning("skill_tools: registry unavailable: %s", exc)
        return {}
    skills = reg.get("skills") if isinstance(reg, dict) else None
    if not isinstance(skills, dict):
        return {}
    return {str(k): v for k, v in skills.items() if isinstance(v, dict)}


def _resolve_name(requested: str, entries: dict[str, dict[str, Any]]) -> str | None:
    """Map what the model typed onto a registry key.

    Models write ``/icdev-build``, ``build``, or ``ICDEV-Build`` about as often as
    the canonical name. Each of those identifies exactly one skill, so resolving
    them beats spending a turn on "skill not found".
    """
    raw = (requested or "").strip().strip("/")
    if not raw:
        return None
    if raw in entries:
        return raw
    candidates = [raw, f"icdev-{raw}", f"icdev-auto-{raw}"]
    for cand in candidates:
        if cand in entries:
            return cand
    lowered = {k.lower(): k for k in entries}
    for cand in candidates:
        hit = lowered.get(cand.lower())
        if hit is not None:
            return hit
    return None


def _skill_md_path(name: str, entry: dict[str, Any]) -> Path | None:
    """Locate a skill's ``SKILL.md`` under this checkout's repo root.

    The registry's ``path`` is stored as it was produced on the machine that
    built the cache, and ``registry.json`` is committed — so a cache built on
    Windows carries ``.agents\\skills\\x\\SKILL.md``, which on POSIX is one
    filename containing backslashes rather than three path segments. Separators
    are normalised before joining, and the conventional layout is tried as a
    fallback, so neither a foreign-OS cache nor a renamed ``name:`` frontmatter
    key leaves a skill unreadable.
    """
    root = _repo_root()
    rel = str(entry.get("path") or "").replace("\\", "/").strip()
    candidates: list[Path] = []
    if rel:
        candidates.append(root.joinpath(*rel.split("/")))
    candidates.append(root.joinpath(*_SKILLS_SUBDIR, name, "SKILL.md"))
    for cand in candidates:
        try:
            resolved = cand.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _read_skill_text(path: Path) -> str:
    """Read a SKILL.md without newline translation, then normalise for a prompt.

    ``newline=""`` keeps the read byte-faithful across platforms; the CRLF pass
    afterwards is a rendering decision, not a rewrite of the file — the model
    reads text, and stray ``\\r`` is noise it would have to ignore.
    """
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# Tool bodies
# ---------------------------------------------------------------------------
def list_skills() -> str:
    """Return the name + description of every indexed skill.

    Bodies are never opened, so the result's size tracks the number of skills and
    not their length.
    """
    entries = _skill_entries()
    if not entries:
        return (
            "(no skills indexed — .agents/skills/ is empty or the registry is "
            "unavailable)"
        )

    lines: list[str] = []
    used = 0
    omitted = 0
    for name in sorted(entries):
        desc = str(entries[name].get("description") or "").strip().replace("\n", " ")
        if len(desc) > _MAX_DESC_CHARS:
            desc = desc[: _MAX_DESC_CHARS - 1].rstrip() + "…"
        line = f"{name} - {desc}" if desc else name
        if used + len(line) + 1 > _MAX_LIST_CHARS:
            omitted += 1
            continue
        lines.append(line)
        used += len(line) + 1

    header = (
        f"{len(entries)} skills available. Call load_skill(name) to read one "
        "skill's full instructions; only load one you intend to follow."
    )
    body = "\n".join(lines)
    if omitted:
        body += f"\n... ({omitted} more omitted — listing size limit reached)"
    return f"{header}\n\n{body}"


def load_skill(name: str) -> str:
    """Return one named skill's full ``SKILL.md`` and record the use.

    Args:
        name: Skill name as shown by ``list_skills`` (``icdev-build``). A bare
            or slash-prefixed form is resolved too.
    """
    requested = (name or "").strip()
    if not requested:
        return "error: 'name' is required (see list_skills)"

    entries = _skill_entries()
    if not entries:
        return (
            "error: no skills indexed — .agents/skills/ is empty or the registry "
            "is unavailable"
        )

    resolved_name = _resolve_name(requested, entries)
    if resolved_name is None:
        return (
            f"error: no skill named {requested!r}. Available: "
            + ", ".join(sorted(entries))
        )

    entry = entries[resolved_name]
    path = _skill_md_path(resolved_name, entry)
    if path is None:
        return (
            f"error: skill {resolved_name!r} is indexed but its SKILL.md is not "
            "present in this checkout"
        )
    try:
        text = _read_skill_text(path)
    except OSError as exc:
        return f"error reading skill {resolved_name!r}: {exc}"

    truncated = ""
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS]
        truncated = f"\n... (truncated at {_MAX_BODY_CHARS} characters)"

    _record_use(resolved_name)

    try:
        rel = path.relative_to(_repo_root()).as_posix()
    except ValueError:  # pragma: no cover — _skill_md_path already confined it
        rel = path.name
    return f"# skill: {resolved_name}\n# source: {rel}\n\n{text}{truncated}"


def _record_use(name: str) -> None:
    """Bump the curator's usage counters. Best-effort — never fails a load.

    Resolved through the module object rather than a from-import so the write is
    observable (and patchable) at call time, and so a checkout without the
    lifecycle module still serves skill bodies.
    """
    try:
        from tools.agent_runtime import skills_lifecycle

        skills_lifecycle.record_use(name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill_tools: record_use(%s) skipped: %s", name, exc)


# ---------------------------------------------------------------------------
# Agent-loop handlers + schemas
# ---------------------------------------------------------------------------
def _handle_list_skills(_inp: dict[str, Any], _stop: threading.Event | None) -> str:
    return list_skills()


def _handle_load_skill(inp: dict[str, Any], _stop: threading.Event | None) -> str:
    return load_skill(str(inp.get("name", "")))


SCHEMAS: dict[str, dict[str, Any]] = {
    "list_skills": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "list_skills",
            "is_read_only": True,
            "description": (
                "List every available ICDEV skill as 'name - description'. A "
                "skill is a vetted procedure for a recurring task. This returns "
                "names and one-line descriptions only, never skill bodies, so it "
                "is cheap to call. Call it first when a task looks like one a "
                "skill might already cover, then load_skill() the one that fits."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "load_skill": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "load_skill",
            "is_read_only": True,
            "description": (
                "Read one named skill's full instructions. Use the name shown by "
                "list_skills. Load a skill only when you intend to follow it — "
                "each load is recorded as a use of that skill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name from list_skills, e.g. 'icdev-build'.",
                    },
                },
                "required": ["name"],
            },
        },
    },
}

HANDLERS: dict[str, ToolHandler] = {
    "list_skills": _handle_list_skills,
    "load_skill": _handle_load_skill,
}


def build_skill_toolset() -> "tuple[list[dict[str, Any]], dict[str, ToolHandler]]":
    """Return ``(tools, handlers)`` for the two skill-disclosure tools."""
    return [SCHEMAS[n] for n in HANDLERS], dict(HANDLERS)


def skill_tool_names() -> list[str]:
    """Return the sorted names of the skill-disclosure tools."""
    return sorted(HANDLERS)
