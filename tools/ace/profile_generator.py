# CUI // SP-CTI
"""ACE profile generator — LLM-assisted co-worker role YAML generation.

Functions
---------
list_profiles()                         → list of {role_id, display_name, source} dicts
suggest_profile_names(description)      → list[str] of name candidates
preview_profile(name, description)      → dict spec (not written to disk)
generate_profile(name, description, *, spec_override) → {role_id, files_written}
delete_profile(role_id)                 → {} or {error}

Generated roles land in ``args/ace/roles/<slug>.yaml`` alongside built-in
roles, and are tagged ``source: generated`` so the UI can badge them
differently.  Built-in roles (no ``source`` key or ``source: builtin``) are
protected from deletion.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_ROLES_DIR = Path(__file__).parents[2] / "args" / "ace" / "roles"
_CANDIDATES_DIR = _ROLES_DIR / "candidates"


def _slug(name: str) -> str:
    """Normalise a display name to a safe role_id slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:48] or "custom_role"


def _load_yaml(path: Path) -> dict:
    import yaml  # PyYAML
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _dump_yaml(data: dict, path: Path) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def list_profiles() -> list[dict[str, Any]]:
    """Return all roles: built-in + generated + candidates."""
    profiles: list[dict[str, Any]] = []
    for yaml_path in sorted(_ROLES_DIR.glob("*.yaml")):
        data = _load_yaml(yaml_path)
        if not data.get("role_id"):
            continue
        profiles.append({
            "role_id": data["role_id"],
            "display_name": data.get("display_name", data["role_id"]),
            "description": (data.get("description") or "")[:120],
            "source": data.get("source", "builtin"),
            "trust_tier": data.get("trust_tier", "yellow"),
        })
    for yaml_path in sorted(_CANDIDATES_DIR.glob("*.yaml")) if _CANDIDATES_DIR.exists() else []:
        data = _load_yaml(yaml_path)
        if not data.get("role_id"):
            continue
        profiles.append({
            "role_id": data["role_id"],
            "display_name": data.get("display_name", data["role_id"]),
            "description": (data.get("description") or "")[:120],
            "source": "candidate",
            "trust_tier": data.get("trust_tier", "yellow"),
        })
    return profiles


def suggest_profile_names(description: str) -> list[str]:
    """Return 3–5 role name suggestions for the given description via LLM."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[{
                "role": "user",
                "content": (
                    f"Suggest 4 concise job-title-style names for an AI co-worker role "
                    f"with the following purpose:\n\n{description}\n\n"
                    "Return one name per line, no numbering, no explanations."
                ),
            }],
            max_tokens=120,
        )
        result = router.invoke("task_decomposition", req)
        raw = (getattr(result, "content", None) or str(result) or "").strip()
        names = [ln.strip().strip("-•*").strip() for ln in raw.splitlines() if ln.strip()]
        return [n for n in names if n][:5] or ["Custom Co-Worker"]
    except Exception:
        return ["Custom Co-Worker", "Specialist Agent", "Domain Expert"]


# Capability names a generated role may hold. Deliberately read-only: write and
# execute agency come from `folder_access` (FileAccessBroker) and `icdev_tools`
# (ToolRunner), neither of which the generator emits at all — a generated role
# gets them only through explicit human promotion.
_ALLOWED_TOOL_PERMISSIONS: frozenset[str] = frozenset({"Read", "Grep", "Glob"})

# Tiers a generated role may claim. "green" is excluded on purpose: it clears
# the CoWorkerThread confidence gate, which is the one thing guaranteeing a
# human looks at a new role before it acts.
_ALLOWED_GENERATED_TIERS: frozenset[str] = frozenset({"red", "yellow"})


def _sanitize_tool_permissions(raw: Any, default: list[str]) -> list[str]:
    """Return only allowlisted capability names, preserving order.

    Falls back to *default* when the model returns nothing usable, so a refusal
    or a malformed response can never widen permissions.
    """
    if not isinstance(raw, (list, tuple)):
        return list(default)
    kept = [p for p in raw if isinstance(p, str) and p in _ALLOWED_TOOL_PERMISSIONS]
    dropped = [p for p in raw if p not in kept]
    if dropped:
        logger.warning("profile_generator: dropped non-allowlisted tool_permissions %r", dropped)
    return kept or list(default)


def _sanitize_trust_tier(raw: Any, default: str) -> str:
    """Return *raw* only if it is a tier a generated role may claim."""
    if isinstance(raw, str) and raw.strip().lower() in _ALLOWED_GENERATED_TIERS:
        return raw.strip().lower()
    logger.warning("profile_generator: refused LLM-supplied trust_tier %r", raw)
    return default


def preview_profile(name: str, description: str = "") -> dict[str, Any]:
    """Generate a full role spec dict without writing to disk.

    Uses LLM to enrich a sparse name + description into a complete YAML spec
    that can be reviewed and edited before committing.
    """
    role_id = _slug(name)
    base_spec: dict[str, Any] = {
        "role_id": role_id,
        "display_name": name,
        "description": description or f"Custom co-worker role: {name}",
        "version": "1.0",
        "source": "generated",
        "trust_tier": "yellow",
        "default_count": 1,
        "max_instances": 2,
        "steps": ["analyze", "execute", "report"],
        "communication": {
            "protocol": "a2a",
            "listen_topics": [],
            "emit_topics": ["task.completed"],
        },
        "llm_function": "task_decomposition",
        "tool_permissions": ["Read", "Grep", "Glob"],
        "genesis_reflex": "maintain",
        "personality": {
            "domain": name,
            "supported_verticals": ["enterprise"],
        },
    }

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        import yaml

        router = LLMRouter()
        req = LLMRequest(
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a YAML role specification for an AI co-worker with:\n"
                    f"  role_id: {role_id}\n"
                    f"  display_name: {name}\n"
                    f"  description: {description or name}\n\n"
                    "Fields to include: steps (list of 3-5 action slugs), "
                    "llm_function (one of: code_generation, task_decomposition, "
                    "compliance_assessment, data_analysis, agent_security, devops), "
                    "tool_permissions (subset of: Read, Write, Edit, Bash, Grep, Glob), "
                    "listen_topics (relevant event topics), "
                    "emit_topics (events this role publishes), "
                    "trust_tier (green/yellow/red).\n"
                    "Return ONLY valid YAML, no markdown fences."
                ),
            }],
            max_tokens=400,
        )
        result = router.invoke("task_decomposition", req)
        raw = (getattr(result, "content", None) or str(result) or "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        enriched = yaml.safe_load(raw) or {}
        # Descriptive fields are taken from the model as-is.
        for field in ("steps", "llm_function"):
            if enriched.get(field):
                base_spec[field] = enriched[field]
        # Security-relevant fields are NOT. The model is asked for them so the
        # preview reflects its intent, but an LLM-authored trust_tier of
        # "green" would clear the confidence gate in CoWorkerThread (learned
        # trust 0.5 < TRUST_SUPERVISED 0.6), letting a freshly generated role
        # begin acting with no human sign-off. Anything outside the allowlist
        # is dropped and the conservative base_spec default stands.
        if enriched.get("tool_permissions"):
            base_spec["tool_permissions"] = _sanitize_tool_permissions(
                enriched["tool_permissions"], base_spec["tool_permissions"]
            )
        if enriched.get("trust_tier"):
            base_spec["trust_tier"] = _sanitize_trust_tier(
                enriched["trust_tier"], base_spec["trust_tier"]
            )
        comm = enriched.get("communication") or {}
        if comm.get("listen_topics"):
            base_spec["communication"]["listen_topics"] = comm["listen_topics"]
        if comm.get("emit_topics"):
            base_spec["communication"]["emit_topics"] = comm["emit_topics"]
    except Exception:
        pass  # fall back to base_spec

    return base_spec


def generate_profile(
    name: str,
    description: str = "",
    *,
    spec_override: dict | None = None,
) -> dict[str, Any]:
    """Generate and persist a new co-worker role YAML.

    Returns {role_id, files_written}.
    """
    spec = spec_override if spec_override else preview_profile(name, description)
    role_id = spec.get("role_id") or _slug(name)
    out_path = _ROLES_DIR / f"{role_id}.yaml"

    # Protect built-in roles from accidental overwrite
    if out_path.exists():
        existing = _load_yaml(out_path)
        if existing.get("source", "builtin") == "builtin":
            raise ValueError(f"Role '{role_id}' is a built-in role and cannot be overwritten.")

    _dump_yaml(spec, out_path)
    files_written = [str(out_path)]

    # Force hot-reload on next access by zeroing the cache timestamp
    try:
        from icdev.tools.ace.controller import ACEController
        loader = ACEController.get_instance()._role_loader
        if hasattr(loader, "_loaded_at"):
            loader._loaded_at = 0.0
    except Exception:
        pass

    return {"role_id": role_id, "files_written": files_written}


def generate_canvas_candidate(
    canvas_key: str,
    display_name: str,
    description: str = "",
) -> dict[str, Any]:
    """Generate a candidate role YAML for a canvas with no existing ACE role.

    Writes to ``args/ace/roles/candidates/<canvas_key>.yaml`` for human review.
    Does NOT write to the live roles/ directory.  Never auto-promotes.

    Args:
        canvas_key: Registry key (e.g. ``"foundry"``, ``"compliance"``).
        display_name: Human-readable canvas name.
        description: Optional canvas description from the component registry.

    Returns:
        ``{canvas_key, role_id, candidate_path, status}``
    """
    out_path = _CANDIDATES_DIR / f"{canvas_key}_coworker.yaml"
    if out_path.exists():
        return {
            "canvas_key": canvas_key,
            "role_id": f"{canvas_key}_coworker",
            "candidate_path": str(out_path),
            "status": "already_exists",
        }

    role_name = f"{display_name} Co-Worker"
    spec = preview_profile(
        role_name,
        description or f"AI co-worker for the {display_name} canvas.",
    )
    # Pin canvas and source metadata
    spec["canvas"] = canvas_key
    spec["source"] = "canvas_candidate"
    spec["role_id"] = f"{canvas_key}_coworker"

    _dump_yaml(spec, out_path)
    return {
        "canvas_key": canvas_key,
        "role_id": spec["role_id"],
        "candidate_path": str(out_path),
        "status": "generated",
    }


def batch_generate_canvas_candidates(
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate candidate role YAMLs for a list of gap canvases.

    Args:
        gaps: List of ``{canvas_key, display_name, enabled}`` dicts, as returned
            by ``canvas_role_gap.detect_gaps()["gaps"]``.

    Returns:
        ``{generated, skipped, errors}`` summary.
    """
    generated: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    for gap in gaps:
        canvas_key = gap.get("canvas_key", "")
        if not canvas_key:
            continue
        try:
            result = generate_canvas_candidate(
                canvas_key=canvas_key,
                display_name=gap.get("display_name", canvas_key),
                description=gap.get("description", ""),
            )
            if result["status"] == "generated":
                generated.append(canvas_key)
            else:
                skipped.append(canvas_key)
        except Exception as exc:
            errors.append({"canvas_key": canvas_key, "error": str(exc)})

    return {"generated": generated, "skipped": skipped, "errors": errors}


def delete_profile(role_id: str) -> dict[str, Any]:
    """Delete a generated profile. Built-in roles are protected."""
    yaml_path = _ROLES_DIR / f"{role_id}.yaml"
    candidate_path = _CANDIDATES_DIR / f"{role_id}.yaml"

    target = None
    if yaml_path.exists():
        target = yaml_path
    elif candidate_path.exists():
        target = candidate_path
    else:
        return {"error": f"Role '{role_id}' not found"}

    data = _load_yaml(target)
    if data.get("source", "builtin") == "builtin":
        return {"error": f"Role '{role_id}' is a built-in role and cannot be deleted"}

    target.unlink()

    try:
        from icdev.tools.ace.role_loader import RoleLoader
        RoleLoader._invalidate_cache()
    except Exception:
        pass

    return {"role_id": role_id, "deleted": True}
