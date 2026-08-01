# CUI // SP-CTI
"""ICDEV Cortex — versioned REST surface (``/cortex/api/v1/*``) (ctx-expose-02).

The ``/cortex`` canvas and the programmatic REST API share ONE
``Blueprint("cortex")`` (the canvas blueprint in ``blueprint.py``).
``register_rest_v1(cortex_bp)`` attaches the seven governed v1 endpoints to that
blueprint, so both the web canvas and the machine surface live under one prefix
and one auth path — no second blueprint, no double registration.

    POST /cortex/api/v1/search     unified retrieval (strategy router + CRAG)
    POST /cortex/api/v1/ask        Cortex Analyst (IQE / NL->SQL)
    POST /cortex/api/v1/complete   free-form completion (governed)
    POST /cortex/api/v1/reason     multi-step reasoning: cot / debate / council (governed)
    POST /cortex/api/v1/classify   single-label classification (governed)
    POST /cortex/api/v1/extract    structured extraction (governed)
    POST /cortex/api/v1/govern     run the TRUST governance chain over text

Identity (tenant / user / classification) is derived SERVER-SIDE from the
authenticated session, never from the client body; ``domain`` is the only
caller-supplied context field and can only narrow, never widen, access.
"""
from __future__ import annotations

import functools
from typing import Callable

from flask import g, jsonify, request

from tools.logging.icdev_logger import get_logger

from . import validators
from .analyst import CortexAnalystError, CortexQueryBlocked
# Import ALL facades from .api — these are the GOVERNED wrappers (TRUST pipeline:
# gateway screen, redaction, grounding, provenance, append-only audit). Importing
# ask/search from .analyst/.search_service would reach the RAW ungoverned impls,
# so the REST /api/v1/search + /ask endpoints would bypass governance entirely.
from .api import ask, classify, complete, extract, reason, search
from .governance import GovernanceBlockedError, GovernancePipeline
from .schemas import CortexContext, CortexResult

logger = get_logger("icdev.cortex.rest_v1")

_API_V1 = "/api/v1"

# Deck-spec allowlist for the /slides surface. Content only — no path-bearing
# keys (see api_v1_slides: image_path on a remote surface is a file-read hole).
_SLIDE_KEYS = (
    "slide_type", "title", "bullets", "speaker_notes", "citations",
    "headings", "cards", "rows", "columns", "mermaid_code", "svg_code",
)
_MAX_SLIDES = 60

# Dashboard-spec allowlist for the /dashboard surface (prem-rpt-02). CONTENT ONLY.
#
# Same discipline as _SLIDE_KEYS above, and for the same reason: on a REMOTE surface,
# any path-bearing key that a renderer honours is an arbitrary-file-read primitive — the
# caller names a file, we render it into a document, and hand the document back.
#
# No spec class carries such a key TODAY. That is not the point. The allowlist is what
# stops the field somebody adds next year from silently becoming one — a blocklist is a
# list of the holes you already know about. Keys mirror the dataclasses in
# tools/viz/spec.py; a new content field must be added here consciously.
_SPEC_KEYS = {
    "chart": ("kind", "title", "chart_type", "categories", "series", "unit", "max_value"),
    "table": ("kind", "title", "headers", "rows"),
    "kpis": ("kind", "title", "tiles"),
    "diagram": ("kind", "title", "nodes", "edges", "layout"),
}
_MAX_TILES = 40

# BOM Evidence Engine limits. A corpus is a folder somebody dragged in, not a data
# lake — and every byte here is parsed, so the ceilings are about protecting the
# host as much as the caller.
_MAX_BOM_DOCUMENTS = 40
_MAX_BOM_BYTES = 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Identity — derived server-side, never from the client body
# ---------------------------------------------------------------------------
def _sec_attr(sec, key: str, default=None):
    """Read a field from ``g.security_context`` (dataclass or dict)."""
    if sec is None:
        return default
    if isinstance(sec, dict):
        return sec.get(key, default)
    return getattr(sec, key, default)


def _server_context(domain: str = "") -> CortexContext:
    """Build a CortexContext from the authenticated session ONLY.

    tenant_id / user_id / classification come from ``g.security_context``
    (set by the dashboard auth middleware) with a fallback to the
    ``g.current_user`` dict. ``domain`` is the sole caller-supplied field —
    it narrows backend selection and cannot widen access.
    """
    sec = getattr(g, "security_context", None)
    user = getattr(g, "current_user", None) or {}

    tenant_id = _sec_attr(sec, "tenant_id") or user.get("tenant_id") or "default"
    user_id = (
        _sec_attr(sec, "user_id")
        or str(user.get("id") or user.get("user_id") or "")
    )
    classification = (
        _sec_attr(sec, "classification")
        or user.get("clearance_level")
        or user.get("classification")
        or "CUI"
    )
    return CortexContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        classification=str(classification),
        domain=domain or "",
    )


def _authenticated() -> bool:
    return bool(getattr(g, "current_user", None))


def _scope_denied(operation: str):
    """Enforce per-operation scopes for Cortex service-key callers.

    Session-authenticated dashboard users carry no ``g.cortex_binding`` and
    pass through unchanged. External icdev_ctx_ callers (compass, idea_lab —
    bound by tools/dashboard/auth.py) must hold ``cortex:<operation>`` from
    their key row. Returns an error response tuple or None.
    """
    binding = getattr(g, "cortex_binding", None)
    if binding is None:
        return None
    scope = f"cortex:{operation}"
    if scope in (binding.get("scopes") or []):
        return None
    return jsonify({
        "error": f"service key lacks required scope '{scope}'",
        "code": "forbidden",
        "label": binding.get("label", ""),
    }), 403


# ---------------------------------------------------------------------------
# Endpoint decorator — auth + JSON parse + uniform error mapping
# ---------------------------------------------------------------------------
def _cortex_api(func: Callable) -> Callable:
    """Wrap a Cortex endpoint with auth, body parsing, and error mapping.

    The wrapped function receives the parsed JSON body dict and returns a
    JSON-serializable dict (HTTP 200). Exceptions map to stable envelopes:
      * validation error            -> 400
      * governance / analyst block   -> 403 (+ serialized GovernanceReport)
      * analyst-unanswerable         -> 422
      * anything else                -> 500
    """

    operation = func.__name__.replace("api_v1_", "")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _authenticated():
            return jsonify({"error": "authentication required"}), 401
        denied = _scope_denied(operation)
        if denied is not None:
            return denied
        try:
            data = request.get_json(silent=True)
        except Exception:
            data = None
        try:
            payload = func(data, *args, **kwargs)
            return jsonify(payload)
        except validators.CortexValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except GovernanceBlockedError as exc:
            return jsonify({
                "error": exc.reason,
                "gate": exc.gate,
                "blocked": True,
                "governance": exc.report.to_dict(),
            }), 403
        except CortexQueryBlocked as exc:
            return jsonify({
                "error": str(exc),
                "blocked": True,
                "governance": exc.governance.to_dict(),
            }), 403
        except CortexAnalystError as exc:
            return jsonify({
                "error": str(exc),
                "governance": exc.governance.to_dict(),
            }), 422
        except Exception as exc:  # pragma: no cover - defensive 500
            logger.exception("cortex REST endpoint failed: %s", exc)
            return jsonify({"error": "internal error"}), 500

    return wrapper


def _governed(
    operation: str,
    prompt: str,
    fn: Callable,
    ctx: CortexContext,
    *,
    retrieval: bool = False,
    context_sources=None,
) -> CortexResult:
    """Run ``fn(governed_prompt)`` through the TRUST governance chain.

    A blocked pre-check raises :class:`GovernanceBlockedError`, which the
    endpoint decorator maps to a 403 governance envelope.
    """
    pipeline = GovernancePipeline(operation=operation)
    result, _report = pipeline.wrap(
        fn, ctx, prompt=prompt, context_sources=context_sources, retrieval=retrieval
    )
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@_cortex_api
def api_v1_search(data):
    """Unified Cortex retrieval. Returns normalized CortexSearchResult rows."""
    params = validators.validate_search(data)
    ctx = _server_context(validators.domain_of(data))
    results = search(
        params["query"], top_k=params["top_k"], strategy=params["strategy"], ctx=ctx
    )
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@_cortex_api
def api_v1_ask(data):
    """Cortex Analyst — natural-language data question over registered scopes."""
    params = validators.validate_ask(data)
    ctx = _server_context(validators.domain_of(data))
    result = ask(
        params["question"],
        mode=params["mode"],
        ctx=ctx,
        canvas=params["canvas"],
        collections=params["collections"],
        summarize=params["summarize"],
    )
    return result.to_dict()


@_cortex_api
def api_v1_complete(data):
    """Free-form completion via the config-routed LLM chain (governed)."""
    params = validators.validate_complete(data)
    ctx = _server_context(validators.domain_of(data))
    kwargs = {"system_prompt": params["system_prompt"]}
    if "max_tokens" in params:
        kwargs["max_tokens"] = params["max_tokens"]
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]
    result = _governed(
        "cortex.complete",
        params["prompt"],
        lambda governed_prompt: complete(governed_prompt, ctx=ctx, **kwargs),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_reason(data):
    """Multi-step reasoning (cot | debate | council) via the config-routed
    chain orchestration (governed)."""
    params = validators.validate_reason(data)
    ctx = _server_context(validators.domain_of(data))
    kwargs = {"system_prompt": params["system_prompt"], "mode": params["mode"]}
    if "max_tokens" in params:
        kwargs["max_tokens"] = params["max_tokens"]
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]
    result = _governed(
        "cortex.reason",
        params["prompt"],
        lambda governed_prompt: reason(governed_prompt, ctx=ctx, **kwargs),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_classify(data):
    """Single-label classification with deterministic air-gap fallback (governed)."""
    params = validators.validate_classify(data)
    ctx = _server_context(validators.domain_of(data))
    labels = params["labels"]
    result = _governed(
        "cortex.classify",
        params["text"],
        lambda governed_text: classify(governed_text, labels, ctx=ctx),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_extract(data):
    """Structured extraction conforming to a caller-supplied JSON schema (governed)."""
    params = validators.validate_extract(data)
    ctx = _server_context(validators.domain_of(data))
    schema = params["schema"]
    result = _governed(
        "cortex.extract",
        params["text"],
        lambda governed_text: extract(governed_text, schema, ctx=ctx),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_govern(data):
    """Run the TRUST governance chain over caller-supplied text.

    A useful dry-run surface: submit text (optionally with the injected
    ``context_sources`` it should be grounded in) and get back the
    GovernanceReport plus the governed (redacted) text. A blocked pre-check
    returns 403 with the report.
    """
    params = validators.validate_govern(data)
    ctx = _server_context(validators.domain_of(data))
    result = _governed(
        params["operation"],
        params["text"],
        lambda governed_text: CortexResult(text=governed_text),
        ctx,
        retrieval=params["retrieval"],
        context_sources=params["context_sources"],
    )
    return {
        "text": result.text,
        "grounded": result.grounded,
        "blocked": result.governance.blocked,
        "governance": result.governance.to_dict(),
    }


@_cortex_api
def api_v1_slides(data):
    """Assemble a themed .pptx from a caller-supplied deck spec (prem-msr-07).

    Deterministic: the caller sends finished slide content and we render it in
    an ICDEV theme. No LLM runs here — this is the presentation layer, not a
    content generator, so it carries no token spend and no governance chain
    (there is no model output to govern).

    The PPTX comes back base64-encoded inside the normal JSON envelope so the
    surface stays uniform for service-key clients.

    SECURITY: the builder honours an ``image_path`` per slide, which on a
    remote surface would be an arbitrary-file-read primitive — any caller could
    embed /etc/passwd or a host secret into a deck we hand back. Slide dicts are
    therefore rebuilt from an allowlist of content-only keys; path-bearing keys
    never reach the builder.
    """
    import base64
    from pathlib import Path

    from tools.slides.constants import DEFAULT_THEME, THEMES
    from tools.slides.pptx_builder import build

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    slides = data.get("slides")
    if not isinstance(slides, list) or not slides:
        raise validators.CortexValidationError("slides must be a non-empty list")
    if len(slides) > _MAX_SLIDES:
        raise validators.CortexValidationError(
            f"too many slides ({len(slides)}); max is {_MAX_SLIDES}")

    theme = data.get("theme") or DEFAULT_THEME
    if theme not in THEMES:
        raise validators.CortexValidationError(
            f"unknown theme '{theme}' (valid: {', '.join(THEMES)})")

    clean = []
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise validators.CortexValidationError(f"slide {index} must be an object")
        # Allowlist — never a blocklist. image_path and friends are dropped.
        clean.append({key: slide[key] for key in _SLIDE_KEYS if key in slide})

    title = str(data.get("title") or "ICDEV™ Presentation")
    path = Path(build(clean, theme=theme, title=title))
    try:
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
    finally:
        path.unlink(missing_ok=True)

    return {
        "filename": path.name,
        "content_type": ("application/vnd.openxmlformats-officedocument"
                         ".presentationml.presentation"),
        "pptx_base64": payload,
        "theme": theme,
        "slide_count": len(clean),
    }


@_cortex_api
def api_v1_win_themes(data):
    """Register cited win themes against an opportunity (prem-recomp-05).

    This is the intake for themes an external capture tool (compass) has PLANNED
    and PROVEN. Registered themes land in ``pg_win_themes``, which
    ``capture_strategy.resolve_strategy`` merges into the strategy block that
    ``response_drafter`` injects into the /proposals + /rfi drafting prompts — so
    a theme pushed here actually shapes the draft rather than merely being graded
    after the fact.

    EVERY THEME MUST CARRY EVIDENCE. An uncited theme is refused, not stored with
    an empty evidence field: these themes are injected into a generative prompt,
    and an unproven claim that reaches a proposal is exactly the failure mode the
    TRUST rules exist to prevent. The sender is expected to have proven them
    (compass flags unproven themes rather than pushing them); this is the
    server-side half of that contract, because "the client checked" is not a
    security property.
    """
    from tools.govcon.win_theme_manager import THEME_TYPES, register_theme

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    opportunity_id = str(data.get("opportunity_id") or "").strip()
    if not opportunity_id:
        raise validators.CortexValidationError("opportunity_id is required")

    themes = data.get("themes")
    if not isinstance(themes, list) or not themes:
        raise validators.CortexValidationError("themes must be a non-empty list")

    registered, refused = [], []
    for index, theme in enumerate(themes):
        if not isinstance(theme, dict):
            raise validators.CortexValidationError(f"theme {index} must be an object")

        statement = str(theme.get("statement") or "").strip()
        if not statement:
            raise validators.CortexValidationError(
                f"theme {index} has no statement")

        theme_type = str(theme.get("theme_type") or "win_theme")
        if theme_type not in THEME_TYPES:
            raise validators.CortexValidationError(
                f"theme_type must be one of {THEME_TYPES}")

        # Citations arrive as either rendered text or structured rows; both must
        # amount to at least one real source.
        evidence = theme.get("evidence")
        if isinstance(evidence, list):
            evidence = "; ".join(
                f"{c.get('claim', '')} [{c.get('source', '')}]".strip()
                for c in evidence if isinstance(c, dict) and c.get("claim")
            )
        evidence = str(evidence or "").strip()

        if not evidence:
            refused.append({
                "statement": statement,
                "reason": ("no supporting evidence — an uncited theme would be "
                           "injected into a drafting prompt as an unproven claim"),
            })
            continue

        result = register_theme(
            opportunity_id,
            theme_type,
            statement,
            supporting_evidence=evidence,
            target_eval_factor=theme.get("target_eval_factor") or None,
            priority=int(theme.get("priority") or 1),
        )
        registered.append({"theme_id": result.get("theme_id"),
                           "statement": statement, "theme_type": theme_type})

    logger.info("win-theme intake: %d registered, %d refused for opportunity %s",
                len(registered), len(refused), opportunity_id)
    return {
        "opportunity_id": opportunity_id,
        "registered": registered,
        "refused": refused,
        "registered_count": len(registered),
        "refused_count": len(refused),
    }


@_cortex_api
def api_v1_staffing_matrix(data):
    """Register EVIDENCED person -> LCAT mappings against an opportunity (prem-pstaff-02).

    The bid side had no people table at all. ``pg_lcat_allocations`` is task -> LCAT ->
    FTE and never names a human; ``pma_personnel`` is post-award, keyed on contract_id.
    So ``program_bridge._gather_key_personnel`` regex-scraped capitalised bigrams out of
    proposal prose — a pattern that matches "Program Manager" and "Technical Approach"
    as readily as it matches a person — and fed them into the Key Personnel volume.

    EVERY MAPPING MUST CARRY EVIDENCE. A person proposed for a labour category with
    nothing behind the claim is refused, not stored with an empty evidence field. It is
    the same defect class as an uncited win theme: it reaches the customer as an
    assertion nobody can defend when they ask "why is she a Senior Systems Engineer?".

    A refusal is NOT an error. Per-person refusals come back in ``refused[]`` with a 200,
    so a compass push of 30 people is not lost because one of them has a thin resume.
    Only STRUCTURAL problems (no opportunity_id, no people, a person that is not an
    object) are 400s.

    Scope: ``cortex:staffing_matrix`` — deliberately NOT in the default grant. A key
    that can search must not silently also be able to staff a bid.
    """
    from tools.govcon.key_personnel import (
        PERSON_SOURCES,
        QUALIFICATION_VERDICTS,
        register_person,
    )

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    opportunity_id = str(data.get("opportunity_id") or "").strip()
    if not opportunity_id:
        raise validators.CortexValidationError("opportunity_id is required")

    people = data.get("people")
    if not isinstance(people, list) or not people:
        raise validators.CortexValidationError("people must be a non-empty list")

    binding = getattr(g, "cortex_binding", None) or {}
    tenant_id = str(binding.get("tenant_id") or "default")
    classification = str(binding.get("classification_ceiling") or "CUI")

    registered, refused = [], []
    for index, person in enumerate(people):
        if not isinstance(person, dict):
            raise validators.CortexValidationError(f"person {index} must be an object")

        source = str(person.get("source") or "compass")
        if source not in PERSON_SOURCES:
            raise validators.CortexValidationError(
                f"person {index}: source must be one of {list(PERSON_SOURCES)}"
            )

        result = register_person(
            opportunity_id=opportunity_id,
            person_ref=str(person.get("person_ref") or "").strip(),
            name=str(person.get("name") or "").strip(),
            proposed_lcat=str(person.get("proposed_lcat") or "").strip(),
            qualification_verdict=str(person.get("qualification_verdict") or "").strip(),
            evidence=person.get("evidence"),
            source=source,
            key_person=bool(person.get("key_person")),
            # The unmet criteria travel WITH a 'gap' verdict. A gap person can still be
            # the right bid — but the bid side must SEE the gap when they decide that
            # and price the risk, not discover it at the debrief.
            gaps=person.get("gaps") or [],
            # The KEY is authoritative for tenant + classification. A request body can
            # never widen its own binding — same rule as every other Cortex intake.
            tenant_id=tenant_id,
            classification=classification,
        )

        if result.get("status") == "registered":
            registered.append({
                "id": result.get("id"),
                "person_ref": person.get("person_ref"),
                "name": person.get("name"),
                "proposed_lcat": person.get("proposed_lcat"),
                "qualification_verdict": person.get("qualification_verdict"),
                "evidence_count": result.get("evidence_count"),
                "key_person": bool(person.get("key_person")),
                "gap_count": len(person.get("gaps") or []),
                "action": result.get("action"),
            })
        else:
            refused.append({
                "person_ref": person.get("person_ref"),
                "name": person.get("name"),
                "reason": result.get("reason"),
            })

    logger.info(
        "staffing-matrix intake: %d registered, %d refused for opportunity %s",
        len(registered), len(refused), opportunity_id,
    )
    return {
        "opportunity_id": opportunity_id,
        "registered": registered,
        "refused": refused,
        "registered_count": len(registered),
        "refused_count": len(refused),
        "verdicts": list(QUALIFICATION_VERDICTS),
    }


@_cortex_api
def api_v1_cost_volume(data):
    """Price a bid from its LCAT allocations (prem-bid-02).

    ``rate_benchmarker.generate_cost_volume()`` already existed and already wrote
    pg_cost_volumes — but it had ZERO callers outside its own CLI main(). It was not
    missing code, it was DEAD code. And it was dead for a reason: its audit write
    INSERTed into a column called ``timestamp`` that does not exist on audit_trail, with
    no try/except, so it raised on every path. It could not run. This gives it its first
    real caller, and a working one.

    UNRATED LABOUR CATEGORIES ARE SURFACED, NEVER GUESSED. The old code priced an LCAT
    with no rate at ``rate = 85.0  # default if no rate set`` — a made-up number on a
    bid, silently loaded through the wrap rates and the price-to-win band until the
    total looked exactly like a real one. Now the volume is refused unless every
    allocation carries a rate; the unrated ones come back with enough detail to go and
    fetch them.

    ``allow_unrated: true`` prices only the rated lines and marks the volume ``partial``
    — for the estimating path that genuinely wants a partial answer. ``partial`` is not
    ``ok``, and nothing downstream may treat it as such.

    Scope: ``cortex:cost_volume`` — NOT in the default grant. A key that can search must
    not silently also be able to put a PRICE on a proposal.
    """
    from tools.govcon.rate_benchmarker import generate_cost_volume

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    opportunity_id = str(data.get("opportunity_id") or "").strip()
    if not opportunity_id:
        raise validators.CortexValidationError("opportunity_id is required")

    # prem-bid-04 — WHO OWNS THE PRICE.
    #
    # ICDEV can compute a volume, but it prices from pg_lcat_allocations, whose
    # hourly_rate is frequently NULL: ICDEV does not hold the supplier rate cards.
    # compass does — it merges ~80 supplier files and knows what an LCAT actually costs
    # from a given vendor on a given date. So compass is the PRICING AUTHORITY, and
    # ICDEV computing its own number would give us two prices for one bid. That is worse
    # than having none: somebody then has to decide which is real, and they will decide
    # it late.
    #
    # A `priced` body means "here is the price, recorded by the party that owns it".
    # Accepting is not believing — the volume is reconciled against its own line items
    # and refused if it declares itself partial. See cost_volume_intake.
    priced = data.get("priced")
    if priced is not None:
        from tools.govcon.cost_volume_intake import PRICING_SOURCES, accept_cost_volume

        source = str(data.get("priced_by") or "compass")
        if source not in PRICING_SOURCES:
            raise validators.CortexValidationError(
                f"priced_by must be one of {list(PRICING_SOURCES)}")

        binding = getattr(g, "cortex_binding", None) or {}
        result = accept_cost_volume(
            opportunity_id=opportunity_id,
            priced=priced,
            source=source,
            # From the KEY, never the body.
            tenant_id=str(binding.get("tenant_id") or "default"),
            classification=str(binding.get("classification_ceiling") or "CUI"),
        )
        logger.info("cost-volume INTAKE: opportunity=%s status=%s source=%s",
                    opportunity_id, result.get("status"), source)
        return result

    contract_type = str(data.get("contract_type") or "ffp").strip().lower()
    allow_unrated = bool(data.get("allow_unrated"))

    result = generate_cost_volume(
        opportunity_id, contract_type, allow_unrated=allow_unrated
    )

    logger.info(
        "cost-volume: opportunity=%s status=%s unrated=%d",
        opportunity_id, result.get("status"), result.get("unrated_count", 0),
    )
    return result


@_cortex_api
def api_v1_dashboard(data):
    """Build and EXPORT a customer-facing dashboard (prem-rpt-02).

    Body: ``{title, tiles: [{spec: {...}}], format: "html"|"pptx"|"pdf",
    classification?, theme?}``. Returns the same envelope the canvas export returns —
    HTML inline, PPTX/PDF base64.

    SECURITY — the /slides ``image_path`` hole is the precedent, and it is the reason
    this endpoint rebuilds every spec from a CONTENT-ONLY ALLOWLIST rather than passing
    the caller's dict through. A viz spec is a nested structure that renderers walk; on
    a REMOTE surface, any path-bearing key a renderer honours is an arbitrary-file-read
    primitive — the caller names a file, we render it into a document, and we hand the
    document back to them. An allowlist, never a blocklist: a blocklist is a list of the
    holes you already know about.

    ``classification`` is taken from the KEY's ceiling, not the body. An export leaves
    the platform by design, so the marking travels with it — and a caller must not be
    able to talk its own export down to UNCLASSIFIED.

    Scope: ``cortex:dashboard`` — NOT in the default grant.
    """
    from tools.bi_dashboard.export import export_dashboard, supported_formats

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    tiles = data.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise validators.CortexValidationError("tiles must be a non-empty list")
    if len(tiles) > _MAX_TILES:
        raise validators.CortexValidationError(
            f"too many tiles ({len(tiles)}); max is {_MAX_TILES}")

    fmt = str(data.get("format") or "html").strip().lower()
    if fmt not in supported_formats():
        raise validators.CortexValidationError(
            f"format must be one of {supported_formats()}, got {fmt!r}")

    clean_tiles = []
    for index, tile in enumerate(tiles):
        if not isinstance(tile, dict):
            raise validators.CortexValidationError(f"tile {index} must be an object")
        spec = tile.get("spec")
        if not isinstance(spec, dict):
            raise validators.CortexValidationError(f"tile {index} has no spec object")
        kind = str(spec.get("kind") or "")
        if kind not in _SPEC_KEYS:
            raise validators.CortexValidationError(
                f"tile {index}: unsupported spec kind {kind!r} "
                f"(allowed: {', '.join(sorted(_SPEC_KEYS))})")
        # ALLOWLIST — never a blocklist. Path-bearing keys never reach a renderer.
        clean_tiles.append({
            "spec": {k: spec[k] for k in _SPEC_KEYS[kind] if k in spec}
        })

    binding = getattr(g, "cortex_binding", None) or {}
    dashboard = {
        "title": str(data.get("title") or "Dashboard"),
        # From the KEY, not the body. A caller cannot mark its own export down.
        "classification": str(binding.get("classification_ceiling") or "CUI"),
        "tiles": clean_tiles,
    }

    result = export_dashboard(dashboard, fmt)
    logger.info("dashboard export: format=%s tiles=%d", fmt, len(clean_tiles))
    result["tile_count"] = len(clean_tiles)
    return result


@_cortex_api
def api_v1_award(data):
    """A won bid becomes a PROPOSED delivery baseline in /cpmp (prem-bid-04).

    This is the crossing where a bid stops being a bid. It creates a cpmp_contracts row
    carrying the price we actually bid, with CLINs generated from the priced allocations —
    the thing that used to come out worth $0.00 while reporting "status: ok" (prem-bid-03).

    The baseline lands as a PROPOSAL: the contract row is 'draft' and the response names
    what contracts staff still have to supply (period of performance, chiefly, because
    proposal_opportunities carries none and inventing dates would be the same failure as
    the $85 default rate). A won bid does not get to self-approve itself into an active
    contract.

    Scope: ``cortex:award`` — NOT in the default grant, and deliberately separate from
    ``cortex:cost_volume``. A key that can PRICE a bid must not silently also be able to
    declare it WON and open a contract against it.
    """
    from tools.govcon.portfolio_manager import transition_from_opportunity

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    opportunity_id = str(data.get("opportunity_id") or "").strip()
    if not opportunity_id:
        raise validators.CortexValidationError("opportunity_id is required")

    result = transition_from_opportunity(
        opportunity_id, created_by=str(data.get("created_by") or "compass")
    )

    if result.get("status") == "error":
        # Not a 500. "The opportunity is not marked won" is a legitimate answer to a
        # legitimate question, and the caller needs the reason, not a stack trace.
        raise validators.CortexValidationError(result.get("message") or "cannot transition")

    logger.info("award: opportunity=%s contract=%s value=%s",
                opportunity_id, result.get("contract_id"), result.get("total_value"))
    return result


@_cortex_api
def api_v1_bom(data):
    """Reconcile a pile of documents into one defensible bill of materials.

    The caller posts the CONTENTS of their documents, base64-encoded, and gets
    back the reconciled lines, the findings, the credibility ladder and the
    pivots. No LLM runs on this path unless the caller opts into adjudication —
    the deterministic engine finds the double-counted licences, the subtotals that
    stopped tracking their own inputs, the line that looks costed and costs
    nothing, and the copy of a workbook that would have doubled every figure in it.

    SECURITY. Documents arrive as BYTES, never as paths. A remote endpoint that
    accepted a filesystem path would be an arbitrary-file-read primitive dressed
    up as a convenience — the caller names /etc/passwd and we obligingly parse it
    into a bill of materials and hand it back. The same trap the /slides surface
    already guards against, and it is worth the extra base64.

    Everything is parsed in a temporary directory and deleted. Nothing a caller
    uploads is persisted here; persistence is the calling product's business, and
    that product knows whose data it is.
    """
    import base64
    import binascii
    import tempfile
    from pathlib import Path

    from tools.bom.credibility import assess
    from tools.bom.derivative import find_derivatives
    from tools.bom.extract_grid import extract_grid
    from tools.bom.findings import analyze_document
    from tools.bom.forensics import analyze as run_forensics
    from tools.bom.lines import extract_lines
    from tools.bom.pivot import build_dataset, pivot as build_pivot, suggest_pivots
    from tools.bom.reconcile import Source, reconcile

    if not isinstance(data, dict):
        raise validators.CortexValidationError("body must be a JSON object")

    documents = data.get("documents")
    if not isinstance(documents, list) or not documents:
        raise validators.CortexValidationError("documents must be a non-empty list")
    if len(documents) > _MAX_BOM_DOCUMENTS:
        raise validators.CortexValidationError(
            f"too many documents ({len(documents)}); max is {_MAX_BOM_DOCUMENTS}")

    with tempfile.TemporaryDirectory(prefix="icdev_bom_") as tmp:
        root = Path(tmp)
        extractions = []
        declared: dict[str, dict] = {}

        for i, doc in enumerate(documents):
            if not isinstance(doc, dict):
                raise validators.CortexValidationError(f"document {i} must be an object")

            name = str(doc.get("filename") or "").strip()
            if not name:
                raise validators.CortexValidationError(f"document {i} needs a filename")
            # The filename is a LABEL, never a path. Basename it: a caller that
            # sends "../../etc/passwd" gets a file called "passwd" in a temp dir,
            # which is exactly as interesting as it deserves to be.
            name = Path(name).name

            try:
                blob = base64.b64decode(str(doc.get("content_base64") or ""), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise validators.CortexValidationError(
                    f"document {i} ({name}): content_base64 is not valid base64"
                ) from exc

            if not blob:
                raise validators.CortexValidationError(f"document {i} ({name}) is empty")
            if len(blob) > _MAX_BOM_BYTES:
                raise validators.CortexValidationError(
                    f"document {i} ({name}) is {len(blob):,} bytes; max is "
                    f"{_MAX_BOM_BYTES:,}")

            path = root / name
            path.write_bytes(blob)

            declared[name] = {
                # Only a human's designation binds. The caller passes theirs
                # through; where they have not ruled, the engine proposes and says
                # it is proposing.
                "role": str(doc.get("role") or "").strip(),
                "credibility_tier": str(doc.get("credibility_tier") or "").strip(),
            }
            extractions.append(extract_grid(path))

        derivations = {d.derived: d for d in find_derivatives(extractions)}

        sources: dict[str, Source] = {}
        source_report = []
        for ext in extractions:
            fx = run_forensics(root / ext.filename)
            proposal = assess(
                ext, fx, derivative_of=(
                    derivations[ext.filename].original
                    if ext.filename in derivations else ""
                ),
            )
            said = declared.get(ext.filename, {})
            tier = said.get("credibility_tier") or proposal.tier
            role = said.get("role") or proposal.role
            set_by = "human" if said.get("credibility_tier") else "ai_proposed"

            sources[ext.filename] = Source(
                source_id=ext.filename,
                filename=ext.filename,
                credibility_tier=tier,
                role=role,
            )
            source_report.append({
                "filename": ext.filename,
                "credibility_tier": tier,
                "role": role,
                "set_by": set_by,
                "rationale": proposal.rationale,
                "derived_from": (
                    derivations[ext.filename].original
                    if ext.filename in derivations else ""
                ),
                "warnings": ext.warnings,
            })

        lines = []
        findings = []
        for ext in extractions:
            lines += extract_lines(ext, source_id=ext.filename)
            findings += analyze_document(ext)

        # No adjudicator is passed. The REST surface runs the engine in --no-llm
        # mode: it cannot hallucinate, because there is nothing in it that could.
        # Adjudication of the ambiguous band is a separate, opt-in call.
        result = reconcile(lines, sources)
        findings += result.findings

        dataset = build_dataset(result.clusters, lines, sources)
        pivots = []
        for spec in suggest_pivots(dataset):
            p = build_pivot(
                dataset,
                rows=spec["rows"], cols=spec["cols"],
                measure=spec["measure"], agg=spec["agg"],
            )
            pivots.append({
                "title": spec["title"],
                "rows": spec["rows"],
                "cols": spec["cols"],
                "table": p.as_table(),
                "note": p.reconciliation_note,
            })

    return {
        "sources": source_report,
        "line_count": len(dataset.rows),
        "cluster_count": len(result.clusters),
        # The honest headline. When several documents each claim to price the same
        # project, this is FALSE and committed_total is a sum rather than a total —
        # and the caller is told so rather than shown a tidy number.
        "is_a_total": not dataset.competing_claims,
        "competing_claims": sorted(dataset.claim_sources) if dataset.competing_claims else [],
        "committed_total": round(dataset.committed_total, 2),
        "open_total": round(dataset.open_total, 2),
        "open_count": sum(1 for r in dataset.rows if not r.committed),
        "lines": [
            {
                "description": r.description,
                "qty": r.qty,
                "unit_price": r.unit_price,
                "extended_price": r.extended_price,
                "committed": r.committed,
                "excluded_reason": r.excluded_reason,
                "dims": r.dims,
            }
            for r in dataset.rows
        ],
        "findings": [
            {
                "type": f.finding_type,
                "kind": f.kind,
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "impact_usd": f.impact_usd,
                "detector": f.detector,
                "evidence": [e.as_dict() for e in f.evidence],
            }
            for f in sorted(
                findings,
                key=lambda f: (
                    ["critical", "high", "medium", "low", "info"].index(f.severity),
                    -(f.impact_usd or 0.0),
                ),
            )
        ],
        "pending_decisions": len(result.pending),
        "pivots": pivots,
        "llm_calls": result.llm_calls,
    }


def api_v1_health():
    """Liveness probe — config + air-gap posture, no LLM call, no auth.

    Registered in PUBLIC_ENDPOINTS (tools/dashboard/auth.py) so external
    consumers' ``is_available()`` probes work without a session or key.
    Status only — never returns data.
    """
    try:
        from .config import airgap_active, load_cortex_config

        load_cortex_config()
        return jsonify({
            "ok": True,
            "status": "healthy",
            "airgap": bool(airgap_active(None)),
            "operations": [
                "search", "ask", "complete", "reason", "classify", "extract", "govern", "intake", "slides", "win_themes", "staffing_matrix", "cost_volume", "dashboard", "award", "bom",
            ],
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "status": "unhealthy", "error": str(exc)[:300]}), 503


def register_rest_v1(cortex_bp) -> None:
    """Attach the ``/cortex/api/v1/*`` endpoints to the shared canvas blueprint.

    Called once from ``blueprint.py`` after the canvas ``cortex_bp`` is defined,
    so the machine surface and the web canvas share a single Blueprint (no
    second registration, no url_prefix drift).
    """
    for name, view in (
        ("search", api_v1_search),
        ("ask", api_v1_ask),
        ("complete", api_v1_complete),
        ("reason", api_v1_reason),
        ("classify", api_v1_classify),
        ("extract", api_v1_extract),
        ("govern", api_v1_govern),
        ("slides", api_v1_slides),
        ("win_themes", api_v1_win_themes),
        ("staffing_matrix", api_v1_staffing_matrix),
        ("cost_volume", api_v1_cost_volume),
        ("dashboard", api_v1_dashboard),
        ("award", api_v1_award),
        ("bom", api_v1_bom),
    ):
        cortex_bp.add_url_rule(
            f"{_API_V1}/{name}", f"api_v1_{name}", view, methods=["POST"]
        )
    cortex_bp.add_url_rule(
        f"{_API_V1}/health", "api_v1_health", api_v1_health, methods=["GET"]
    )
