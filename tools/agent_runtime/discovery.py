# CUI // SP-CTI
"""Tool auto-discovery + OpenAI schema generation for the SAG runtime (sag-reg-01).

The standalone agent runtime needs a *registry* of callable tools, each described
by an OpenAI function-calling schema, that can be assembled into bounded toolsets
(sag-reg-02) and dispatched by :func:`icdev.tools.llm.agent_loop.run_agent_loop`.

Rather than invent a third registration pattern, this module derives agent-loop
tool specs from the two registries that already exist:

1. **The MCP registry** — ``tools.mcp.tool_registry.TOOL_REGISTRY`` already maps
   ``name -> {module, handler, description, input_schema}`` for 440+ tools. Its
   ``input_schema`` is JSON-Schema, so wrapping it in the OpenAI
   ``{"type": "function", "function": {...}}`` envelope is nearly free — one MCP
   registration serves both the MCP surface and the agent loop (the 8-point
   new-tool checklist already requires MCP registration).
2. **The built-in starter toolset** — ``tools.agent_runtime.builtin_tools`` — whose
   schemas are already in OpenAI format.

For *new* first-party functions that are not MCP-registered, a lightweight
:func:`tool` decorator (or a ``__tool_schema__`` attribute) marks a callable as a
tool; when no explicit schema is supplied, :func:`schema_from_callable` synthesises
one from the signature, type hints, and a Google-style docstring — stdlib only
(``inspect`` / ``typing`` / ``re``), no new heavy deps.

Conditional availability is expressed with a ``check`` callable on the
:class:`ToolSpec`; :func:`build_registry` drops specs whose check returns falsey so
a tool that depends on an optional backend never reaches the model. Finally the
serialisable half of the registry (schemas + dispatch coordinates, not live
callables) can be cached to JSON via :func:`write_cache` for sub-2s startup.

This module is intentionally **discovery only**: it produces schemas and dispatch
coordinates. Turning ``{module, handler}`` coordinates into agent-loop
``handler(input, stop_event) -> str`` callables — with task_id/stop_event
injection and the safety-layer hook — is sag-reg-02's job.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.discovery")

# A conditional-availability probe: returns True if the tool should be offered.
CheckFn = Callable[[], bool]


# ---------------------------------------------------------------------------
# Repo-root resolution (upward sentinel walk — mirrors sit at different depths)
# ---------------------------------------------------------------------------
def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)
_DEFAULT_CACHE = _REPO_ROOT / ".tmp" / "agent_tool_registry.json"


# ---------------------------------------------------------------------------
# ToolSpec — one discovered tool
# ---------------------------------------------------------------------------
@dataclass
class ToolSpec:
    """A single discovered tool.

    Attributes:
        name: Unique tool name (the OpenAI function name).
        schema: OpenAI function-calling schema
            (``{"type": "function", "function": {...}}``).
        source: Where it came from — ``"mcp"``, ``"builtin"``, or ``"decorated"``.
        read_only: True if the tool performs no state mutation (enables the agent
            loop's parallel read-only dispatch and skips the safety gate).
        module: Import path of the module holding the handler (for lazy dispatch).
        handler: Attribute name of the handler within ``module``.
        check: Optional availability probe; when it returns falsey the tool is
            excluded from the assembled registry.
        callable: The live function object, when discovered via the decorator.
    """

    name: str
    schema: dict[str, Any]
    source: str = "mcp"
    read_only: bool = False
    module: Optional[str] = None
    handler: Optional[str] = None
    check: Optional[CheckFn] = field(default=None, repr=False)
    callable: Optional[Callable[..., Any]] = field(default=None, repr=False)

    def available(self) -> bool:
        """Return True unless a ``check`` probe is present and returns falsey."""
        if self.check is None:
            return True
        try:
            return bool(self.check())
        except Exception as exc:  # noqa: BLE001 — a broken probe hides the tool
            logger.warning("discovery: check for %r raised: %s", self.name, exc)
            return False

    def to_cache_entry(self) -> dict[str, Any]:
        """Return the JSON-serialisable half (no live callables/probes)."""
        return {
            "name": self.name,
            "schema": self.schema,
            "source": self.source,
            "read_only": self.read_only,
            "module": self.module,
            "handler": self.handler,
        }


# ---------------------------------------------------------------------------
# JSON-schema type mapping + docstring parsing (schema synthesis)
# ---------------------------------------------------------------------------
_PRIMITIVE_JSON_TYPES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Best-effort map a Python type annotation to a JSON-schema fragment.

    Handles primitives, ``Optional[T]``/``Union`` (uses the first non-None arm),
    ``list[T]`` (with ``items``), ``dict``, and ``Literal[...]`` (as an enum).
    Unknown types degrade to ``{"type": "string"}`` — a permissive default that
    never crashes schema generation.
    """
    if tp is inspect.Parameter.empty or tp is None or tp is type(None):
        return {"type": "string"}

    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if origin is typing.Literal:
        enum = [a for a in args]
        base = _PRIMITIVE_JSON_TYPES.get(type(enum[0]), "string") if enum else "string"
        return {"type": base, "enum": enum}

    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if not non_none:
            return {"type": "string"}
        return python_type_to_json_schema(non_none[0])

    if origin in (list, typing.List):  # type: ignore[attr-defined]
        item = args[0] if args else str
        return {"type": "array", "items": python_type_to_json_schema(item)}

    if origin in (dict, typing.Dict):  # type: ignore[attr-defined]
        return {"type": "object"}

    if isinstance(tp, type):
        return {"type": _PRIMITIVE_JSON_TYPES.get(tp, "string")}

    return {"type": "string"}


# Google-style "Args:" section, tolerant of "arg (type): desc" and "arg: desc".
_ARG_LINE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.+)$")
_SECTION_HEADS = {
    "args:", "arguments:", "parameters:", "params:",
    "returns:", "return:", "raises:", "yields:", "examples:", "example:", "note:", "notes:",
}


def parse_docstring(doc: Optional[str]) -> tuple[str, dict[str, str]]:
    """Parse a docstring into ``(summary, {param_name: description})``.

    ``summary`` is the text before the first section header (collapsed to a single
    line). Parameter descriptions are read from a Google-style ``Args:`` block.
    Best-effort: any parse failure yields an empty description map.
    """
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary_parts: list[str] = []
    params: dict[str, str] = {}
    in_args = False
    summary_done = False
    for raw in lines:
        low = raw.strip().lower()
        if low in _SECTION_HEADS:
            in_args = low in ("args:", "arguments:", "parameters:", "params:")
            summary_done = True
            continue
        if in_args:
            m = _ARG_LINE.match(raw)
            if m:
                params[m.group(1)] = m.group(2).strip()
            # continuation lines for the previous arg are ignored (best-effort)
            continue
        if summary_done:
            continue
        if not low and summary_parts:
            # blank line after summary text ends the summary (keep scanning for
            # an Args: section that may follow).
            summary_done = True
            continue
        if low:
            summary_parts.append(raw.strip())
    return " ".join(summary_parts).strip(), params


_SKIP_PARAMS = {"self", "cls", "stop", "stop_event", "task_id", "_stop", "_task_id"}


def schema_from_callable(
    fn: Callable[..., Any],
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Synthesise an OpenAI function schema from a callable.

    Uses the signature + resolved type hints + Google-style docstring. Parameters
    named like injected runtime plumbing (``stop_event``, ``task_id``, ``self``)
    are omitted from the model-facing schema. A parameter is *required* iff it has
    no default. Returns the full ``{"type": "function", ...}`` envelope.
    """
    tool_name = name or getattr(fn, "__name__", "tool")
    doc = inspect.getdoc(fn)
    summary, param_docs = parse_docstring(doc)
    desc = description or summary or f"Invoke {tool_name}."

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 — forward refs / missing modules
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    if sig is not None:
        for pname, param in sig.parameters.items():
            if pname in _SKIP_PARAMS:
                continue
            if param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            ann = hints.get(pname, param.annotation)
            frag = python_type_to_json_schema(ann)
            if pname in param_docs:
                frag = {**frag, "description": param_docs[pname]}
            properties[pname] = frag
            if param.default is inspect.Parameter.empty:
                required.append(pname)

    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    return {
        "type": "function",
        "is_read_only": read_only,
        "function": {
            "name": tool_name,
            "is_read_only": read_only,
            "description": desc,
            "parameters": parameters,
        },
    }


# ---------------------------------------------------------------------------
# @tool decorator + explicit markers
# ---------------------------------------------------------------------------
def tool(
    _fn: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    read_only: bool = False,
    check: Optional[CheckFn] = None,
):
    """Mark a callable as an agent tool (opt-in discovery marker).

    Usage::

        @tool(read_only=True)
        def list_widgets(kind: str) -> str:
            \"\"\"List widgets of a kind.\"\"\"
            ...

    Attaches ``__tool_schema__`` (synthesised on first access) and ``__tool_meta__``
    so :func:`discover_decorated` can pick the function up. The wrapped function is
    returned unchanged, so it stays directly callable/testable.
    """

    def _apply(fn: Callable[..., Any]) -> Callable[..., Any]:
        meta = {
            "name": name or getattr(fn, "__name__", "tool"),
            "description": description,
            "read_only": read_only,
            "check": check,
        }
        fn.__tool_meta__ = meta  # type: ignore[attr-defined]
        # Lazily-resolvable schema: honour a pre-set __tool_schema__ if present.
        if not getattr(fn, "__tool_schema__", None):
            fn.__tool_schema__ = schema_from_callable(  # type: ignore[attr-defined]
                fn,
                name=meta["name"],
                description=description,
                read_only=read_only,
            )
        return fn

    if _fn is not None:  # bare @tool usage
        return _apply(_fn)
    return _apply


def _spec_from_decorated(fn: Callable[..., Any], module: str) -> Optional[ToolSpec]:
    schema = getattr(fn, "__tool_schema__", None)
    meta = getattr(fn, "__tool_meta__", None) or {}
    if not schema:
        return None
    fn_name = (schema.get("function") or {}).get("name") or meta.get("name")
    if not fn_name:
        return None
    return ToolSpec(
        name=fn_name,
        schema=schema,
        source="decorated",
        read_only=bool(meta.get("read_only", schema.get("is_read_only", False))),
        module=module,
        handler=getattr(fn, "__name__", None),
        check=meta.get("check"),
        callable=fn,
    )


def discover_decorated(module_names: list[str]) -> list[ToolSpec]:
    """Import each module and collect its ``@tool``-decorated callables."""
    specs: list[ToolSpec] = []
    for modname in module_names:
        try:
            mod = importlib.import_module(modname)
        except Exception as exc:  # noqa: BLE001
            logger.warning("discovery: cannot import %r: %s", modname, exc)
            continue
        for _attr, obj in vars(mod).items():
            if callable(obj) and getattr(obj, "__tool_schema__", None):
                # only pick up tools defined in this module, not re-imports
                if getattr(obj, "__module__", None) not in (modname, None):
                    continue
                spec = _spec_from_decorated(obj, modname)
                if spec is not None:
                    specs.append(spec)
    return specs


# ---------------------------------------------------------------------------
# MCP-registry derivation
# ---------------------------------------------------------------------------
# Heuristic: MCP tools whose names clearly only read state. The agent loop only
# uses this to enable parallel dispatch; the safety layer (sag-safe-01) is the
# real mutation gate, so a conservative guess here is safe.
_READ_ONLY_PREFIXES = (
    "get_", "list_", "search_", "query_", "check_", "status", "health",
    "lookup", "read_", "show_", "detect_", "scan_", "summary", "validate_",
)


def _guess_read_only(name: str) -> bool:
    low = name.lower()
    return low.startswith(_READ_ONLY_PREFIXES) or low.endswith(("_status", "_summary"))


def schema_from_mcp_entry(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Wrap an MCP ``TOOL_REGISTRY`` entry in the OpenAI function envelope."""
    input_schema = entry.get("input_schema") or {"type": "object", "properties": {}}
    read_only = _guess_read_only(name)
    return {
        "type": "function",
        "is_read_only": read_only,
        "function": {
            "name": name,
            "is_read_only": read_only,
            "description": (entry.get("description") or f"Invoke {name}.").strip(),
            "parameters": input_schema,
        },
    }


def load_mcp_registry() -> dict[str, dict[str, Any]]:
    """Return ``TOOL_REGISTRY`` (empty dict if the module cannot be imported)."""
    try:
        from tools.mcp.tool_registry import TOOL_REGISTRY

        return dict(TOOL_REGISTRY)
    except Exception as exc:  # noqa: BLE001
        logger.warning("discovery: MCP TOOL_REGISTRY unavailable: %s", exc)
        return {}


def discover_mcp_tools(names: Optional[set[str]] = None) -> list[ToolSpec]:
    """Derive :class:`ToolSpec`\\ s from the MCP registry.

    Args:
        names: Optional whitelist of tool names to include. When ``None``, every
            MCP tool is derived (use a whitelist to keep a toolset bounded —
            sag-reg-02 supplies these from YAML bundles).
    """
    registry = load_mcp_registry()
    specs: list[ToolSpec] = []
    for tname, entry in registry.items():
        if names is not None and tname not in names:
            continue
        if not isinstance(entry, dict):
            continue
        specs.append(
            ToolSpec(
                name=tname,
                schema=schema_from_mcp_entry(tname, entry),
                source="mcp",
                read_only=_guess_read_only(tname),
                module=entry.get("module"),
                handler=entry.get("handler"),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Built-in starter toolset derivation
# ---------------------------------------------------------------------------
def discover_builtin_tools() -> list[ToolSpec]:
    """Derive :class:`ToolSpec`\\ s from the built-in starter toolset."""
    try:
        from tools.agent_runtime import builtin_tools as bt

        schemas = bt._SCHEMAS  # noqa: SLF001 — first-party, stable within the pkg
    except Exception as exc:  # noqa: BLE001
        logger.warning("discovery: builtin toolset unavailable: %s", exc)
        return []
    specs: list[ToolSpec] = []
    for tname, schema in schemas.items():
        fn = (schema.get("function") or {})
        specs.append(
            ToolSpec(
                name=tname,
                schema=schema,
                source="builtin",
                read_only=bool(fn.get("is_read_only", schema.get("is_read_only", False))),
                module="tools.agent_runtime.builtin_tools",
                handler=None,  # dispatched via build_builtin_toolset(), not by name
            )
        )
    return specs


# ---------------------------------------------------------------------------
# Registry assembly + JSON cache
# ---------------------------------------------------------------------------
def build_registry(
    *,
    include_builtin: bool = True,
    include_mcp: bool = True,
    mcp_names: Optional[set[str]] = None,
    decorated_modules: Optional[list[str]] = None,
    extra_specs: Optional[list[ToolSpec]] = None,
    apply_checks: bool = True,
) -> dict[str, ToolSpec]:
    """Assemble the unified tool registry as ``{name: ToolSpec}``.

    Sources are layered so later ones win on a name clash: MCP → built-in →
    decorated → ``extra_specs``. Specs whose :meth:`ToolSpec.available` probe is
    falsey are dropped when ``apply_checks`` is True.
    """
    specs: list[ToolSpec] = []
    if include_mcp:
        specs.extend(discover_mcp_tools(mcp_names))
    if include_builtin:
        specs.extend(discover_builtin_tools())
    if decorated_modules:
        specs.extend(discover_decorated(decorated_modules))
    if extra_specs:
        specs.extend(extra_specs)

    registry: dict[str, ToolSpec] = {}
    for spec in specs:
        if apply_checks and not spec.available():
            logger.info("discovery: %r excluded (availability check false)", spec.name)
            continue
        registry[spec.name] = spec  # later source wins
    return registry


def write_cache(
    registry: dict[str, ToolSpec], path: Optional[Path] = None
) -> Path:
    """Persist the serialisable half of ``registry`` to JSON for fast startup."""
    dest = Path(path) if path else _DEFAULT_CACHE
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "tools": [spec.to_cache_entry() for spec in registry.values()],
    }
    dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return dest


def load_cache(path: Optional[Path] = None) -> list[dict[str, Any]]:
    """Load cached tool entries (schemas + dispatch coordinates). ``[]`` if none.

    Live callables and ``check`` probes are not cached; the dispatch layer
    (sag-reg-02) rehydrates handlers lazily from ``module``/``handler``.
    """
    src = Path(path) if path else _DEFAULT_CACHE
    if not src.exists():
        return []
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("discovery: cannot read cache %s: %s", src, exc)
        return []
    tools = data.get("tools") if isinstance(data, dict) else None
    return list(tools) if isinstance(tools, list) else []


def to_openai_tools(registry: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    """Return the OpenAI function-schema list for ``run_agent_loop(tools=...)``."""
    return [spec.schema for spec in registry.values()]


def main(argv: Optional[list[str]] = None) -> int:
    """CLI: build the registry and print a JSON summary (also refreshes cache)."""
    import argparse

    parser = argparse.ArgumentParser(description="SAG tool auto-discovery")
    parser.add_argument("--no-mcp", action="store_true", help="skip MCP derivation")
    parser.add_argument("--write-cache", action="store_true", help="write JSON cache")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    registry = build_registry(include_mcp=not args.no_mcp)
    if args.write_cache:
        dest = write_cache(registry)
        logger.info("discovery: wrote cache to %s", dest)
    summary = {
        "tool_count": len(registry),
        "by_source": _count_by_source(registry),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Discovered {summary['tool_count']} tools: {summary['by_source']}")
    return 0


def _count_by_source(registry: dict[str, ToolSpec]) -> dict[str, int]:
    out: dict[str, int] = {}
    for spec in registry.values():
        out[spec.source] = out.get(spec.source, 0) + 1
    return out


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
