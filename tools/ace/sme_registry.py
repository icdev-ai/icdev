# CUI // SP-CTI
"""One entry point for "get me an SME for this domain, creating it if needed".

ACE carries two disconnected role systems:

* ``tools/ace/roles/<role_id>/SOUL.md`` — persona *identity*, read by
  ``soul_manager.build_identity_preamble`` for the one-shot ``ace_persona_query``
  advisory path. 24 of these exist.
* ``args/ace/roles/<role_id>.yaml`` — *executable* co-worker role (steps,
  trust_tier, tool_permissions), read by ``RoleLoader`` for team launches. 90 of
  these exist.

Only 17 role_ids have both. ``persona_generator.get_or_generate_persona()``
writes the SOUL.md half only, so a runtime-generated SME could advise but could
never be staffed onto a team — which is the gap that made "spin up SMEs as
needed" impossible.

``ensure_sme()`` closes it: one call, one normalized domain label, both halves,
or neither.

Safety
------
The model authors WHO the expert is. ``tools.ace.role_policy`` decides WHAT it
may do, from hand-authored bundles in ``args/ace/sme_capability_bundles.yaml``.
Every generated role is stamped with a bundle and then re-validated before it is
written; a role that fails validation is not written at all.

Fresh SMEs are ``trust_tier: red`` on purpose — see the trust-tier note in
``sme_capability_bundles.yaml``. The existing HITL confidence gate reviews run #1
for free.

LLM-agnostic: every model call goes through ``LLMRouter`` by logical function
name. No model IDs appear here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import yaml

from icdev.tools.ace import role_policy
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Similarity at or above which an existing role is considered to already cover
#: the requested domain, so nothing is generated. Chosen to be forgiving: the
#: cost of reusing a near-match is a slightly-off persona, while the cost of a
#: false miss is a permanent near-duplicate in a 90-role catalog.
REUSE_THRESHOLD = 0.60

#: Below this, the domain is clearly novel. The band between the two is where an
#: LLM adjudication pass belongs (see sme-gap-02); until that lands, the
#: conservative choice in the band is to reuse rather than proliferate.
NOVEL_THRESHOLD = 0.35

_INDEX_FILENAME = "_generated_smes.json"


@dataclass
class SmeResult:
    """Outcome of an ``ensure_sme`` call."""

    role_id: str
    status: str  # "cached" | "generated" | "reused"
    domain_label: str
    capability_bundle: str
    soul_path: str = ""
    role_yaml_path: str = ""
    matched_existing: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "status": self.status,
            "domain_label": self.domain_label,
            "capability_bundle": self.capability_bundle,
            "soul_path": self.soul_path,
            "role_yaml_path": self.role_yaml_path,
            "matched_existing": self.matched_existing,
        }


# ---------------------------------------------------------------------------
# Paths — resolved via the same helpers the loaders use, never __file__ math.
# ---------------------------------------------------------------------------


def _role_yaml_dir() -> Path:
    """Directory RoleLoader reads executable roles from."""
    from icdev._paths import get_data_path

    return get_data_path("args") / "ace" / "roles"


def _soul_dir() -> Path:
    """Directory soul_manager reads persona identities from.

    Delegates to soul_manager so the ``ICDEV_ACE_ROLES_DIR`` override is
    honoured — persona_generator resolves this from ``__file__`` instead, which
    is why its writes land in only one of the two mirrored trees.
    """
    try:
        from icdev.tools.ace import soul_manager

        resolver = getattr(soul_manager, "_roles_dir", None)
        if callable(resolver):
            return Path(resolver())
    except Exception as exc:  # noqa: BLE001
        logger.debug("sme_registry: soul_manager dir resolution failed: %s", exc)
    from icdev.tools.ace import persona_generator

    return Path(persona_generator._ROLES_DIR)


def _index_path() -> Path:
    return _soul_dir() / _INDEX_FILENAME


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt index must not block generation
        logger.warning("sme_registry: index unreadable at %s; starting fresh", path)
        return {}


def _save_index(index: dict[str, Any]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower().strip())
    return s.strip("_")[:48] or "custom_specialist"


# ---------------------------------------------------------------------------
# Duplicate suppression
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text).lower()) if len(t) > 2}


#: Sequence similarity is discounted relative to token overlap. Character
#: similarity over-rewards a shared prefix: "network security" scores 0.69
#: against "network doc auditor" — a *documentation* auditor — purely because
#: both start with "network". Token evidence is the honest signal; the sequence
#: ratio only breaks ties and catches morphology ("management"/"manager") that
#: token equality misses.
_SEQUENCE_WEIGHT = 0.75



def _similarity(label: str, candidate_text: str) -> float:
    """Blend token overlap with (discounted) sequence similarity."""
    a, b = _tokens(label), _tokens(candidate_text)
    jaccard = len(a & b) / len(a | b) if (a or b) else 0.0
    ratio = SequenceMatcher(None, label.lower(), str(candidate_text).lower()).ratio()
    return max(jaccard, ratio * _SEQUENCE_WEIGHT)


def _adjudicate_near_miss(domain_label: str) -> str:
    """Ask whether an existing role already covers *domain_label*.

    Returns a real ``role_id`` or ``""``. Constrained deliberately: the model
    chooses from a supplied list or answers NONE — it never invents a name, so
    the worst case is an unnecessary generation, never a ghost role that fails
    ``role_not_found`` at launch.

    Any failure returns "" (generate), because falling back to "create a
    specialist" is recoverable while wrongly reusing an unrelated role silently
    gives the user the wrong expert.
    """
    neighbours = _catalog_listing()
    if not neighbours:
        return ""

    listing = "\n".join(f"- {rid}: {desc}" for rid, desc in neighbours)
    valid = {rid for rid, _ in neighbours}

    try:
        from icdev.tools.llm.provider import LLMRequest
        from icdev.tools.llm.router import LLMRouter

        response = LLMRouter().invoke(
            "task_decomposition",
            LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        f"Domain needing an expert: {domain_label}\n\n"
                        f"Existing experts:\n{listing}\n\n"
                        "Does one of the existing experts already cover that domain "
                        "well enough that creating a new specialist would be a "
                        "near-duplicate? Answer with exactly one role id from the "
                        "list, or the single word NONE. No explanation."
                    ),
                }],
                max_tokens=24,
                temperature=0.0,
            ),
        )
        answer = (getattr(response, "content", "") or "").strip().split()[0].strip(".,`'\"")
    except Exception as exc:  # noqa: BLE001
        logger.debug("sme_registry: adjudication unavailable (%s); generating", exc)
        return ""

    if answer in valid:
        return answer
    return ""


def _catalog_listing() -> list[tuple[str, str]]:
    """Every catalog role as ``(role_id, short_label)``.

    The adjudicator is shown the WHOLE catalog rather than a lexically
    pre-filtered shortlist. Pre-filtering was the bug: ranking by token overlap
    could not connect "container image vulnerability scanning" to
    `security_analyst`, whose description says "reviews vulnerabilities", and
    hand-rolled stemming made it worse by producing inconsistent stems
    ("vulnerability" -> vulnerab, "vulnerabilities" -> vulnerabil). A candidate
    filtered out can never be chosen, so a recall bug there is invisible and
    permanent.

    ~90 roles at a few tokens each is a small prompt, and it is paid only in the
    near-miss band -- never on a clear hit or a clear miss. Correct recall for a
    trivial cost beats a clever filter that silently drops the right answer.
    """
    role_dir = _role_yaml_dir()
    if not role_dir.exists():
        return []

    out: list[tuple[str, str]] = []
    for path in sorted(role_dir.glob("*.yaml")):
        try:
            spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        role_id = str(spec.get("role_id") or path.stem)
        personality = spec.get("personality") or {}
        domain = personality.get("domain", "") if isinstance(personality, dict) else ""
        label = str(domain or spec.get("display_name") or role_id.replace("_", " "))
        out.append((role_id, label[:60]))
    return out


def find_existing_role(domain_label: str) -> tuple[str, float]:
    """Return the best-matching existing role id and its score.

    Compares against ``role_id``, ``display_name`` and ``personality.domain`` of
    every role YAML on disk, so a request for "network security" resolves to the
    shipped ``security_analyst`` rather than minting a near-duplicate.
    """
    best_id, best_score = "", 0.0
    role_dir = _role_yaml_dir()
    if not role_dir.exists():
        return best_id, best_score

    for path in sorted(role_dir.glob("*.yaml")):
        try:
            spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — one bad YAML must not break lookup
            continue
        role_id = str(spec.get("role_id") or path.stem)
        candidates = [
            role_id.replace("_", " "),
            str(spec.get("display_name") or ""),
            str((spec.get("personality") or {}).get("domain") or ""),
        ]
        score = max((_similarity(domain_label, c) for c in candidates if c), default=0.0)
        if score > best_score:
            best_id, best_score = role_id, score

    return best_id, best_score


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

_ROLE_PROMPT = """You are defining a subject-matter expert for an autonomous \
co-worker system.

Domain: {label}
Context: {description}

Return ONLY valid YAML (no markdown fences) with exactly these keys:
  display_name: a short human title for this expert
  description: one sentence on what this expert is for
  steps: a list of 3-5 short snake_case action slugs this expert performs, in order
  domain: a 1-3 word canonical domain name
  capabilities: a list of 3-6 short capability phrases
  rules: a list of 2-4 short operating rules this expert follows

Do NOT include permissions, tools, trust levels, file paths, or commands. \
Those are assigned separately and anything you supply for them is discarded."""


def _generate_role_fields(domain_label: str, description: str) -> dict[str, Any]:
    """Ask the router for the descriptive half of a role spec.

    Returns ``{}`` on any failure; the caller falls back to a deterministic
    skeleton so a provider outage degrades the persona rather than the feature.
    """
    try:
        from icdev.tools.llm.provider import LLMRequest
        from icdev.tools.llm.router import LLMRouter

        response = LLMRouter().invoke(
            "task_decomposition",
            LLMRequest(
                messages=[{
                    "role": "user",
                    "content": _ROLE_PROMPT.format(label=domain_label, description=description),
                }],
                max_tokens=600,
                temperature=0.2,
            ),
        )
        text = (getattr(response, "content", "") or "").strip()
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
        parsed = yaml.safe_load(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:  # noqa: BLE001 — degrade, never block
        logger.warning("sme_registry: role field generation failed for %r: %s", domain_label, exc)
        return {}


def _build_role_spec(
    role_id: str,
    domain_label: str,
    description: str,
    bundle_name: str,
) -> dict[str, Any]:
    """Assemble a complete role spec: model-authored identity + policy-applied limits."""
    fields = _generate_role_fields(domain_label, description)

    steps = fields.get("steps")
    if not isinstance(steps, list) or not steps:
        steps = ["analyze", "assess", "recommend"]
    steps = [_slug(s) for s in steps][:5]

    spec: dict[str, Any] = {
        "role_id": role_id,
        "display_name": str(fields.get("display_name") or domain_label.title())[:80],
        "description": str(
            fields.get("description") or f"Subject-matter expert for {domain_label}."
        )[:400],
        "version": "1.0",
        "default_count": 1,
        "max_instances": 2,
        "steps": steps,
        "llm_function": "task_decomposition",
        "genesis_reflex": "audit",
        "communication": {
            "protocol": "a2a",
            "listen_topics": [],
            "emit_topics": ["task.completed"],
        },
        "personality": {
            "domain": str(fields.get("domain") or domain_label)[:60],
            "capabilities": [str(c)[:60] for c in (fields.get("capabilities") or [])][:6],
            "supported_verticals": ["enterprise"],
        },
        "rules": [str(r)[:200] for r in (fields.get("rules") or [])][:4],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_from": description[:300],
    }

    # Security fields come from the bundle, never from the model.
    role_policy.apply_bundle(spec, bundle_name)
    return spec


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_sme(
    domain_description: str,
    *,
    capability_bundle: str | None = None,
    allow_reuse: bool = True,
) -> SmeResult:
    """Return a team-capable SME role id for *domain_description*.

    Resolution order:

    1. Normalise the description to a stable domain label (the same LLM
       normalisation ``persona_generator`` uses, so "blockchain" and "crypto and
       web3 tech" converge on one persona rather than two).
    2. Return a previously generated SME for that label if both halves survive.
    3. Reuse a sufficiently similar existing role rather than minting a
       near-duplicate of the 90-role catalog.
    4. Otherwise generate BOTH halves and index the result.

    Raises ``ValueError`` on empty input and ``PermissionError`` if the
    generated role fails capability validation — in which case nothing is
    written.
    """
    description = (domain_description or "").strip()
    if not description:
        raise ValueError("domain_description is required")

    from icdev.tools.ace import persona_generator

    domain_label = persona_generator._normalize_domain_label(description)
    role_id = _slug(domain_label)
    bundle_name, _ = role_policy.get_bundle(capability_bundle)

    # 2. Previously generated, still intact on disk?
    index = _load_index()
    soul_path = _soul_dir() / role_id / "SOUL.md"
    yaml_path = _role_yaml_dir() / f"{role_id}.yaml"
    if role_id in index and soul_path.exists() and yaml_path.exists():
        return SmeResult(
            role_id=role_id,
            status="cached",
            domain_label=domain_label,
            capability_bundle=str(index[role_id].get("capability_bundle") or bundle_name),
            soul_path=str(soul_path),
            role_yaml_path=str(yaml_path),
        )

    # 3. Does the shipped catalog already cover this?
    if allow_reuse and role_id not in index:
        match_id, score = find_existing_role(domain_label)

        # Clear match — reuse, no LLM needed.
        if match_id and score >= REUSE_THRESHOLD:
            logger.info(
                "sme_registry: reusing existing role %r for %r (similarity %.2f)",
                match_id, domain_label, score,
            )
            return SmeResult(
                role_id=match_id,
                status="reused",
                domain_label=domain_label,
                capability_bundle="",
                role_yaml_path=str(_role_yaml_dir() / f"{match_id}.yaml"),
                matched_existing=match_id,
            )

        # Near-miss band. Lexical similarity is genuinely undecided here:
        # "container image vulnerability scanning" scores 0.38 against the
        # catalog yet `security_analyst` covers it perfectly, while "maritime
        # insurance underwriting" scores 0.36 and is genuinely new. Telling
        # those apart needs meaning, not string overlap, so this is the one
        # place worth paying for a model call — and only in the band, never on
        # a clear hit or a clear miss.
        #
        # This is what keeps the catalog from silting up as more industries
        # arrive: without it, every adjacent phrasing of a covered domain mints
        # another near-duplicate.
        if match_id and NOVEL_THRESHOLD <= score < REUSE_THRESHOLD:
            adjudicated = _adjudicate_near_miss(domain_label)
            if adjudicated:
                logger.info(
                    "sme_registry: adjudicated %r -> existing role %r (lexical %.2f)",
                    domain_label, adjudicated, score,
                )
                index.setdefault(role_id, {})
                index[role_id] = {
                    "domain_label": domain_label,
                    "domain_description": description,
                    "resolved_to": adjudicated,
                    "resolved_by": "adjudication",
                }
                _save_index(index)
                return SmeResult(
                    role_id=adjudicated,
                    status="reused",
                    domain_label=domain_label,
                    capability_bundle="",
                    role_yaml_path=str(_role_yaml_dir() / f"{adjudicated}.yaml"),
                    matched_existing=adjudicated,
                )

    # 4. Generate both halves. Validate BEFORE writing anything.
    spec = _build_role_spec(role_id, domain_label, description, bundle_name)
    role_policy.assert_valid(spec, bundle_name)

    persona = persona_generator.get_or_generate_persona(description)
    generated_role_id = str(persona.get("role_id") or role_id)
    if generated_role_id != role_id:
        # persona_generator slugs the same normalised label, so a divergence
        # means the two halves would be filed under different ids — which is
        # exactly the split this function exists to prevent.
        logger.warning(
            "sme_registry: persona role_id %r != registry role_id %r; using persona id",
            generated_role_id, role_id,
        )
        role_id = generated_role_id
        spec["role_id"] = role_id
        yaml_path = _role_yaml_dir() / f"{role_id}.yaml"

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    index[role_id] = {
        "domain_label": domain_label,
        "domain_description": description,
        "capability_bundle": bundle_name,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "role_yaml": True,
    }
    _save_index(index)

    _invalidate_role_cache()

    logger.info("sme_registry: generated SME %r (bundle %r)", role_id, bundle_name)
    return SmeResult(
        role_id=role_id,
        status="generated",
        domain_label=domain_label,
        capability_bundle=bundle_name,
        soul_path=str(_soul_dir() / role_id / "SOUL.md"),
        role_yaml_path=str(yaml_path),
    )


def _invalidate_role_cache() -> None:
    """Force RoleLoader to see a newly written role immediately.

    Without this the role is invisible for up to ``_CACHE_TTL`` seconds, which
    is long enough for a just-approved team launch to fail ``RoleNotFoundError``.
    """
    try:
        from icdev.tools.ace.role_loader import reset_role_parse_cache

        reset_role_parse_cache()
    except Exception as exc:  # noqa: BLE001
        logger.debug("sme_registry: role cache invalidation skipped: %s", exc)
