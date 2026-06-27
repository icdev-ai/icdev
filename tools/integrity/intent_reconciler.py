# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Intent reconciler (Modes A + B).

Two reconciliation strategies share one finding shape and one persistence path:

  * **Mode B (provenance-blind / claim-based)** — ``reconcile_blind`` — the
    **primary external-code path**. When a third-party artifact arrives with no
    formal RTM / PRD, SIPA reconciles the *exercised* capability manifest against
    the author's *claimed* set (README / docstrings / declared purpose) and flags
    the gap as ``undisclosed_capability`` (see below).

  * **Mode A (provenance-aware)** — ``reconcile_aware`` — the artifact *does* have
    a provenance handle (``project_id`` / ``session_id``), so SIPA can build the
    Requirements Traceability Matrix
    (:func:`tools.requirements.traceability_builder.build_rtm`) and read the
    ``intake_requirements`` text. It derives an **ALLOWED-capability set** by
    mapping each requirement's prose through the *same* deterministic lexicon
    ``claim_parser`` uses (a requirement that says "notify by email" authorizes
    ``network_egress``; "run the packaged binary" authorizes ``process_exec``).
    Every capability the code exercises that **no requirement authorizes** is the
    *semantic backdoor* case and is emitted as an ``unauthorized_capability``
    finding (severity from the capability's inherent risk). Requirements with no
    implementing code are surfaced as **coverage gaps** (reusing the RTM's
    ``review_traceability`` rows). Mode A still runs the intrinsic-risk pass, so a
    decode-then-exec shape is flagged ``critical`` even if a requirement authorizes
    it.

When a third-party artifact arrives with no formal RTM / PRD (the Mode B input),
SIPA still has two signals:

  * the **exercised** capability manifest from
    :func:`tools.integrity.capability_extractor.extract` — *what the code can
    actually do*, derived from the AST (never executed); and
  * the **claimed** capability set from
    :func:`tools.integrity.claim_parser.parse_claim` — *what the author says it
    does*, derived from README/docstrings/declared-purpose prose.

``reconcile_blind(manifest, claim)`` judges the first against the second and the
*intrinsic* danger of the code itself, producing ``integrity_findings`` rows in
the exact shape the scanner adapters emit (``source_scanner='reconciliation'``):

  1. **Undisclosed-capability pass.** For every capability the manifest exercises
     that the claim never implies, emit an ``undisclosed_capability`` finding. A
     "JSON formatter" whose code opens a socket has exercised a behavior it never
     disclosed — that gap *is* the Mode B signal. Severity is derived from the
     capability's inherent risk (``constants.RISK_WEIGHTS_CAPABILITY``): an
     undisclosed ``network_egress`` is ``high``, an undisclosed ``process_exec``
     or ``dynamic_code`` is ``critical``.

  2. **Intrinsic-risk pass.** Some shapes are dangerous *regardless* of what the
     author claims. ``dynamic_code`` combined with ``obfuscation`` in the same
     file is the decode-then-exec backdoor shape (``payload = b64decode(...);
     exec(payload)``); it is flagged ``critical`` (``dangerous_api``) even when a
     README cheerfully discloses both. The extractor's per-record
     ``obfuscated_input`` taint link is the strongest form of this signal.

Pure-Python + ``constants`` + the sibling extractor/claim modules. The only
side-effecting entrypoints (``reconcile_and_persist`` / ``assess_blind`` with an
``assessment_id``) append to ``integrity_findings`` via the same RLS-aware path
(``_insert_finding`` / ``_caller_context``) the scanners and capability writer
use, so reconciliation findings can never drift from the rest.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from tools.integrity.constants import (
    RISK_WEIGHTS_CAPABILITY,
    SEVERITY,
)
from tools.integrity.db.init_db import init_db

# Reuse ingest's context/backend/insert helpers so the reconciliation INSERT and
# the tenant/classification stamping match the scanner + capability writers exactly.
from tools.integrity.ingest import _backend_of, _caller_context, _insert_finding

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.intent_reconciler")

# This module is the disclosed-vs-exercised reconciler — its findings are tagged
# with this scanner so the UI / risk scorer can group them.
SOURCE_SCANNER = "reconciliation"

# Cap how many call sites a single finding's ``detail`` carries — a backdoored
# blob could exercise the same capability hundreds of times; the finding stays
# auditable without ballooning the persisted JSON.
_MAX_SITES = 25

# integrity_config.yaml lives at the repo root; this module is three levels deep
# (tools/integrity/intent_reconciler.py -> repo root).
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "integrity_config.yaml"


def _load_config() -> dict:
    """Load ``integrity_config.yaml``; tolerate a missing/empty file with ``{}``.

    Mirrors :func:`tools.integrity.engine._load_config` so the LLM-assist toggle
    reads the same file the rest of SIPA does. Never raises: a missing/unreadable
    config simply means LLM assist stays off and the deterministic path runs.
    """
    try:
        import yaml

        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001 — config is advisory; default to deterministic-only
        return {}


def _load_safe_filesystem_modules() -> frozenset[str]:
    """Load the known_safe_filesystem_modules allowlist from integrity_config.yaml.

    Returns module paths (forward-slash, repo-relative) whose 'filesystem'
    unauthorized_capability findings are suppressed in Mode A self-scans.
    Only affects ICDEV's own tools/ tree; external artifacts are unaffected.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_filesystem_modules") or [])
    except Exception:
        return frozenset()


_SAFE_FS_MODULES: frozenset[str] = _load_safe_filesystem_modules()


def _is_safe_filesystem_module(file_path: Optional[str]) -> bool:
    """True when ``file_path`` matches an entry in known_safe_filesystem_modules.

    Normalises separators so ``tools\\llm\\provider_health.py`` matches the
    config's ``tools/llm/provider_health.py`` regardless of OS.
    """
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_FS_MODULES)


def _load_safe_process_exec_modules() -> frozenset[str]:
    """Load the known_safe_process_exec_modules allowlist from integrity_config.yaml.

    Returns module paths (forward-slash, repo-relative) whose 'process_exec'
    unauthorized_capability findings are suppressed in Mode A self-scans.
    Only affects ICDEV's own tools/ tree; external artifacts are unaffected.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_process_exec_modules") or [])
    except Exception:
        return frozenset()


_SAFE_PROCESS_EXEC_MODULES: frozenset[str] = _load_safe_process_exec_modules()


def _is_safe_process_exec_module(file_path: Optional[str]) -> bool:
    """True when ``file_path`` matches an entry in known_safe_process_exec_modules.

    Normalises separators so ``tools\\testing\\cleanup_utils.py`` matches the
    config's ``tools/testing/cleanup_utils.py`` regardless of OS.
    """
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_PROCESS_EXEC_MODULES)


def _load_safe_crypto_modules() -> frozenset[str]:
    """Load the known_safe_crypto_modules allowlist from integrity_config.yaml.

    Returns module paths (forward-slash, repo-relative) whose 'crypto'
    unauthorized_capability findings are suppressed in Mode A self-scans.
    Only affects ICDEV's own tools/ tree; external artifacts are unaffected.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_crypto_modules") or [])
    except Exception:
        return frozenset()


_SAFE_CRYPTO_MODULES: frozenset[str] = _load_safe_crypto_modules()


def _is_safe_crypto_module(file_path: Optional[str]) -> bool:
    """True when ``file_path`` matches an entry in known_safe_crypto_modules.

    Normalises separators so ``tools\\gateway\\adapters\\botframework_base.py``
    matches the config's ``tools/gateway/adapters/botframework_base.py`` regardless
    of OS.
    """
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_CRYPTO_MODULES)


def _load_safe_obfuscation_modules() -> frozenset[str]:
    """Load the known_safe_obfuscation_modules allowlist from integrity_config.yaml.

    Returns module paths (forward-slash, repo-relative) whose 'obfuscation'
    unauthorized_capability findings are suppressed in Mode A self-scans.
    Covers base64 encoding used for HTTP Basic Auth headers, binary data transport
    (images, SAML, JWT, VSDX export), signing operations, and security detection.
    Only affects ICDEV's own tools/ tree; external artifacts are unaffected.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_obfuscation_modules") or [])
    except Exception:
        return frozenset()


_SAFE_OBFUSCATION_MODULES: frozenset[str] = _load_safe_obfuscation_modules()


def _is_safe_obfuscation_module(file_path: Optional[str]) -> bool:
    """True when ``file_path`` matches an entry in known_safe_obfuscation_modules.

    Normalises separators so ``tools\\auth\\saml.py`` matches the config's
    ``tools/auth/saml.py`` regardless of OS.
    """
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_OBFUSCATION_MODULES)


def _load_safe_serialization_modules() -> frozenset[str]:
    """Load the known_safe_serialization_modules allowlist from integrity_config.yaml.

    Returns module paths whose 'serialization' unauthorized_capability findings are
    suppressed. Covers modules that pickle/marshal internal-only artifacts (e.g. ML
    models trained and loaded exclusively within the platform — never from untrusted
    input). Only affects ICDEV's own tools/ tree.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_serialization_modules") or [])
    except Exception:
        return frozenset()


_SAFE_SERIALIZATION_MODULES: frozenset[str] = _load_safe_serialization_modules()


def _is_safe_serialization_module(file_path: Optional[str]) -> bool:
    """True when ``file_path`` matches an entry in known_safe_serialization_modules."""
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_SERIALIZATION_MODULES)


def _load_platform_authorized_capabilities() -> frozenset[str]:
    """Load the platform_authorized_capabilities list from integrity_config.yaml.

    Returns capability-type strings that are universally authorized across the
    ICDEV platform and therefore suppressed in Mode A self-scans without needing
    a per-module entry in a known_safe_* list.  Only affects ICDEV's own tools/
    tree; external-artifact assessments are unaffected.

    Intended for low-risk capabilities used pervasively for legitimate platform
    operations (e.g. 'crypto' for content fingerprinting and data integrity) where
    per-module allowlist entries would be impractical.  The stronger
    dangerous_api co-presence rule (dynamic_code + obfuscation taint) still fires
    regardless of this list.
    """
    try:
        data = _load_config()
        return frozenset(data.get("platform_authorized_capabilities") or [])
    except Exception:
        return frozenset()


_PLATFORM_AUTHORIZED_CAPABILITIES: frozenset[str] = _load_platform_authorized_capabilities()


def _load_safe_colocation_files() -> frozenset[str]:
    """Load the known_safe_colocation_files allowlist from integrity_config.yaml.

    Files listed here are exempt from the WEAKER dynamic_code+obfuscation
    co-presence sub-rule (dynamic code and base64 present in the same file but
    in completely unrelated functions). The STRONGER taint-link sub-rule
    (obfuscated_input flag — direct ``exec(b64decode(payload))`` pattern) still
    fires for all files regardless of this list.

    Use for files where dynamic import (optional-dependency probing via
    importlib/``__import__``) and base64 (image encoding, protocol decode, etc.)
    co-exist in unrelated, non-interacting functions — the two capabilities are
    incidentally present but not linked into a backdoor shape.
    """
    try:
        data = _load_config()
        return frozenset(data.get("known_safe_colocation_files") or [])
    except Exception:
        return frozenset()


_SAFE_COLOCATION_FILES: frozenset[str] = _load_safe_colocation_files()


def _is_safe_colocation_file(file_path: Optional[str]) -> bool:
    """True when ``file_path`` is exempt from the co-presence sub-rule only."""
    if not file_path:
        return False
    normalised = file_path.replace("\\", "/")
    return any(normalised.endswith(m) for m in _SAFE_COLOCATION_FILES)


# --------------------------------------------------------------------------- #
# Severity derivation — capability inherent risk -> integrity_findings.severity
# --------------------------------------------------------------------------- #
# Bands over constants.RISK_WEIGHTS_CAPABILITY (0.0-1.0). Ordered high->low; the
# first band whose threshold the weight meets wins. Chosen so the task's anchors
# hold: network_egress (0.90) -> high, process_exec (0.95)/dynamic_code (1.00) ->
# critical, and crypto (0.40) -> low.
_SEVERITY_BANDS: list[tuple[float, str]] = [
    (0.95, "critical"),
    (0.80, "high"),
    (0.60, "medium"),
    (0.30, "low"),
]


def _severity_for_weight(weight: float) -> str:
    """Map a capability's inherent risk weight onto ``constants.SEVERITY``."""
    try:
        w = float(weight)
    except (TypeError, ValueError):
        return "info"
    for threshold, sev in _SEVERITY_BANDS:
        if w >= threshold:
            return sev if sev in SEVERITY else "info"
    return "info"


def _severity_for_capability(cap_type: str) -> str:
    """Severity an *undisclosed* capability of this type warrants."""
    return _severity_for_weight(RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0))


# --------------------------------------------------------------------------- #
# Input normalization — tolerate the rich manifest or a bare capability set
# --------------------------------------------------------------------------- #
def _normalize_manifest(manifest: Any) -> list[dict]:
    """Coerce ``manifest`` into a list of capability records.

    Accepts the canonical :func:`capability_extractor.extract` output (a list of
    ``{file_path, function_name, capability_type, evidence, line_start, ...}``
    dicts) verbatim, and degrades gracefully for callers that only have a set /
    list of capability-type *strings* (each becomes a minimal record so the
    undisclosed pass still fires).
    """
    if manifest is None:
        return []
    records: list[dict] = []
    for item in manifest:
        if isinstance(item, dict) and item.get("capability_type"):
            records.append(item)
        elif isinstance(item, str):
            records.append({"capability_type": item, "evidence": {}})
    return records


def _normalize_claim(claim: Any) -> set[str]:
    """Coerce ``claim`` into the set of *claimed* capability-type strings.

    Accepts a :func:`claim_parser.parse_claim` result (a dict with
    ``claimed_capabilities``), a bare set / list of capability strings, or
    ``None`` (the pure-blind case where nothing is disclosed).
    """
    if claim is None:
        return set()
    if isinstance(claim, dict):
        return set(claim.get("claimed_capabilities", ()) or ())
    if isinstance(claim, (set, frozenset, list, tuple)):
        return {c for c in claim if isinstance(c, str)}
    return set()


def _site(rec: dict) -> dict:
    """Compact one capability record into a finding-``detail`` call site."""
    ev = rec.get("evidence") or {}
    return {
        "function": rec.get("function_name"),
        "line_start": rec.get("line_start"),
        "line_end": rec.get("line_end"),
        "api": ev.get("api"),
        "evidence": ev,
    }


def _earliest_line(records: list[dict]) -> Optional[int]:
    """Lowest ``line_start`` across records (the anchor line for a finding)."""
    lines = [r.get("line_start") for r in records if isinstance(r.get("line_start"), int)]
    return min(lines) if lines else None


# --------------------------------------------------------------------------- #
# Pass 1 — undisclosed capabilities (exercised but never claimed)
# --------------------------------------------------------------------------- #
def _undisclosed_findings(records: list[dict], claimed: set[str]) -> list[dict]:
    """One ``undisclosed_capability`` finding per (file, capability) gap.

    Records are grouped by ``(file_path, capability_type)`` so multiple call
    sites of the same undisclosed capability in one file collapse into a single
    actionable finding (anchored at the earliest line, every site retained in
    ``detail.sites`` up to :data:`_MAX_SITES`).
    """
    # Preserve first-seen order for stable output across runs.
    groups: dict[tuple[Optional[str], str], list[dict]] = {}
    order: list[tuple[Optional[str], str]] = []
    for rec in records:
        cap = rec.get("capability_type")
        if not cap or cap in claimed:
            continue
        key = (rec.get("file_path"), cap)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    findings: list[dict] = []
    claimed_sorted = sorted(claimed)
    for key in order:
        file_path, cap = key
        group = groups[key]
        weight = RISK_WEIGHTS_CAPABILITY.get(cap, 0.0)
        findings.append(
            {
                "source_scanner": SOURCE_SCANNER,
                "finding_type": "undisclosed_capability",
                "severity": _severity_for_weight(weight),
                "file_path": file_path,
                "line": _earliest_line(group),
                "detail": {
                    "capability_type": cap,
                    "reason": (
                        f"code exercises '{cap}' but no claim source "
                        f"(README / docstring / declared purpose) discloses it"
                    ),
                    "risk_weight": weight,
                    "occurrences": len(group),
                    "claimed_capabilities": claimed_sorted,
                    "sites": [_site(r) for r in group[:_MAX_SITES]],
                },
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Pass 2 — intrinsic risk (dangerous regardless of the claim)
# --------------------------------------------------------------------------- #
def _intrinsic_findings(records: list[dict]) -> list[dict]:
    """Flag intrinsically dangerous shapes, ignoring what the author claims.

    The headline rule: ``dynamic_code`` co-located with ``obfuscation`` in one
    file is the decode-then-exec backdoor shape and is ``critical``. The
    extractor's ``obfuscated_input`` taint flag (``exec(payload)`` where
    ``payload = b64decode(...)``) is the strongest form of the same signal and is
    sufficient on its own. At most one such finding is emitted per file.
    """
    by_file: dict[Optional[str], dict[str, list[dict]]] = {}
    order: list[Optional[str]] = []
    for rec in records:
        cap = rec.get("capability_type")
        if cap not in ("dynamic_code", "obfuscation"):
            continue
        fp = rec.get("file_path")
        if fp not in by_file:
            by_file[fp] = {"dynamic_code": [], "obfuscation": []}
            order.append(fp)
        by_file[fp][cap].append(rec)

    findings: list[dict] = []
    for fp in order:
        dyn = by_file[fp]["dynamic_code"]
        obf = by_file[fp]["obfuscation"]
        tainted = [r for r in dyn if (r.get("evidence") or {}).get("obfuscated_input")]
        # Fire on a direct decode->exec taint link (strongest signal — always fires),
        # or on plain co-presence of dynamic code and obfuscation in the same file.
        # Co-presence is suppressed for files in known_safe_colocation_files where
        # the two capabilities are in unrelated functions (e.g. importlib probing
        # optional deps alongside base64 image encoding) — the taint check still fires.
        co_present = dyn and obf and not _is_safe_colocation_file(fp)
        if not (tainted or co_present):
            continue
        signals = []
        if tainted:
            signals.append("dynamic_code(obfuscated_input)")
        if co_present:
            signals.append("dynamic_code+obfuscation co-located")
        anchor = _earliest_line(tainted or dyn or obf)
        findings.append(
            {
                "source_scanner": SOURCE_SCANNER,
                "finding_type": "dangerous_api",
                "severity": "critical",
                "file_path": fp,
                "line": anchor,
                "detail": {
                    "rule": "dynamic_code+obfuscation",
                    "reason": (
                        "runtime code execution combined with obfuscation/decoding "
                        "— the decode-then-exec backdoor shape; dangerous regardless "
                        "of any disclosure"
                    ),
                    "signals": signals,
                    "capabilities": ["dynamic_code", "obfuscation"],
                    "dynamic_sites": [_site(r) for r in dyn[:_MAX_SITES]],
                    "obfuscation_sites": [_site(r) for r in obf[:_MAX_SITES]],
                },
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# Optional LLM-assisted second opinion (advisory; deterministic is primary)
# --------------------------------------------------------------------------- #
# The deterministic rule-based reconciliation above ALWAYS runs and is the
# fallback. This pass is a *purely advisory* second opinion: when
# ``integrity_config.yaml`` ``llm_assist.enabled`` is true AND a provider is
# reachable, an LLM judges the claim/requirement prose against the exercised
# capability manifest and returns a match/mismatch verdict + rationale. It never
# changes a deterministic finding's severity or the verdict — it only annotates
# the result so a HITL reviewer gets a natural-language second read. Air-gap safe:
# disabled config or any LLM error degrades silently to deterministic-only.

# JSON Schema the router forces the model onto, so we get a structured verdict
# rather than parsing free text. ``judgement`` is the headline: does the claimed
# purpose plausibly account for everything the code actually exercises?
_ADVISORY_SCHEMA = {
    "type": "object",
    "properties": {
        "judgement": {"type": "string", "enum": ["match", "mismatch"]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["judgement", "rationale"],
}


def _manifest_summary(records: list[dict]) -> dict:
    """Compact, LLM-friendly summary of the exercised capability manifest.

    Keeps the payload small and deterministic: the distinct capability types, a
    per-type occurrence count, and the files touched. No source code is sent.
    """
    by_capability: dict[str, int] = {}
    files: list[str] = []
    for rec in records:
        cap = rec.get("capability_type")
        if cap:
            by_capability[cap] = by_capability.get(cap, 0) + 1
        fp = rec.get("file_path")
        if fp and fp not in files:
            files.append(fp)
    return {
        "exercised_capabilities": sorted(by_capability),
        "by_capability": by_capability,
        "files": files[:_MAX_SITES],
    }


def _try_parse_json(text: str) -> Any:
    """Best-effort JSON extraction from a model's free-text reply (fenced or raw)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return None
    return None


def llm_assist_enabled(config: Optional[dict] = None) -> bool:
    """True iff ``integrity_config.yaml`` ``llm_assist.enabled`` is set."""
    cfg = config if config is not None else _load_config()
    return bool(((cfg or {}).get("llm_assist") or {}).get("enabled"))


def llm_second_opinion(
    claim_or_requirement_text: str,
    capability_manifest_summary: dict,
    *,
    config: Optional[dict] = None,
    router: Any = None,
) -> Optional[dict]:
    """Optional LLM second opinion on claim/requirement vs. exercised manifest.

    Returns an advisory dict
    ``{"source": "llm", "advisory": True, "judgement": "match"|"mismatch",
    "rationale": str, "confidence": float, "function_key": str}`` when LLM assist
    is enabled AND a provider serves the request; otherwise ``None``.

    This is *never* on the critical path: the caller has already computed the
    deterministic findings. Any failure — assist disabled, ``ICDEV_NO_LLM`` set,
    no provider in the chain, a malformed reply — returns ``None`` so the
    deterministic result stands alone. Air-gap safe by construction.

    Args:
        claim_or_requirement_text: the author's claim (Mode B) or the authorizing
            requirement prose (Mode A) the manifest is judged against.
        capability_manifest_summary: the :func:`_manifest_summary` rollup of what
            the code actually exercises (no source code is sent to the provider).
        config: pre-loaded ``integrity_config.yaml`` dict (tests inject this);
            defaults to :func:`_load_config`.
        router: an ``LLMRouter`` instance to reuse (tests inject a fake); defaults
            to a fresh ``LLMRouter()``.
    """
    cfg = config if config is not None else _load_config()
    llm_cfg = (cfg or {}).get("llm_assist") or {}
    if not llm_cfg.get("enabled"):
        return None  # deterministic-only by configuration
    function_key = llm_cfg.get("function_key") or "intent_reconciliation"

    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        # Respect the global air-gap / no-LLM switch without a network round-trip.
        if getattr(router, "is_no_llm_mode", None) and router.is_no_llm_mode():
            return None

        from tools.llm.provider import LLMRequest

        prompt = (
            "You are a software-integrity reviewer giving an ADVISORY second "
            "opinion. A deterministic analyzer has already produced the primary "
            "verdict; your judgement only annotates it.\n\n"
            "Decide whether the CLAIMED PURPOSE plausibly accounts for every "
            "capability the code actually EXERCISES. If the code exercises a "
            "capability the claim does not justify (e.g. a 'JSON formatter' that "
            "opens network sockets or spawns subprocesses), answer 'mismatch'.\n\n"
            f"CLAIM / REQUIREMENT:\n{(claim_or_requirement_text or '(none provided)')[:2000]}\n\n"
            f"EXERCISED CAPABILITY MANIFEST:\n{json.dumps(capability_manifest_summary)[:2000]}\n\n"
            "Return JSON: judgement (one of 'match'|'mismatch'), rationale "
            "(one or two sentences), confidence (0..1)."
        )
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=400,
            output_schema=_ADVISORY_SCHEMA,
            skip_injection_scan=True,  # trusted internal pipeline call
        )
        resp = router.invoke(function=function_key, request=req)
        verdict = getattr(resp, "structured_output", None)
        if not verdict and getattr(resp, "content", None):
            verdict = _try_parse_json(resp.content)
        if not isinstance(verdict, dict):
            return None
        judgement = verdict.get("judgement") or verdict.get("verdict")
        if judgement not in ("match", "mismatch"):
            return None
        try:
            confidence = float(verdict.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        return {
            "source": "llm",
            "advisory": True,
            "judgement": judgement,
            "rationale": str(verdict.get("rationale", "")),
            "confidence": confidence,
            "function_key": function_key,
        }
    except Exception as exc:  # noqa: BLE001 — advisory only; never break reconciliation
        logger.warning("intent_reconciler: LLM second opinion unavailable (%s)", exc)
        return None


def _attach_advisory(
    result: dict,
    claim_or_requirement_text: str,
    records: list[dict],
    *,
    config: Optional[dict] = None,
    router: Any = None,
) -> dict:
    """Compute the optional LLM advisory and store it under ``result['llm_advisory']``.

    Always sets the key (``None`` when assist is off / unavailable) so callers can
    rely on its presence. Mutates and returns ``result``.
    """
    result["llm_advisory"] = llm_second_opinion(
        claim_or_requirement_text,
        _manifest_summary(records),
        config=config,
        router=router,
    )
    return result


# --------------------------------------------------------------------------- #
# Text-based capability scanner (for runtime behavioral monitoring)
# --------------------------------------------------------------------------- #
_TEXT_CAP_PATTERNS: list[tuple[Any, str]] = []  # populated lazily below


def _build_text_patterns() -> list[tuple[Any, str]]:
    """Compile regex patterns for detecting capabilities in plain text output."""
    patterns = [
        (r"\beval\s*\(|\bexec\s*\(|\bcompile\s*\(|\b__import__\s*\(|importlib\.import_module\s*\(", "dynamic_code"),
        (r"base64\.[a-z0-9_]*decode|binascii\.a2b_[a-z]+|bytes\.fromhex\s*\(|codecs\.decode|zlib\.decompress\s*\(", "obfuscation"),
        (r"subprocess\.\w+\s*\(|os\.system\s*\(|os\.popen\s*\(|os\.exec[vle]\w*\s*\(|pty\.spawn\s*\(", "process_exec"),
        (r"socket\.socket\s*\(|requests\.\w+\s*\(|urllib\.request\.\w+\s*\(|http\.client\.HTTP[SC]*Connection|httpx\.\w+\s*\(", "network_egress"),
        (r"\bopen\s*\([^)]*[\"'][aw+bx]|os\.remove\s*\(|os\.unlink\s*\(|shutil\.(?:copy|move|rmtree)\s*\(", "filesystem"),
        (r"os\.getenv\s*\(|os\.environ\b|keyring\.\w+\s*\(|dotenv\.\w+\s*\(", "env_secret"),
        (r"pickle\.loads?\s*\(|marshal\.loads?\s*\(|yaml\.(?:load|unsafe_load|full_load)\s*\(", "serialization"),
    ]
    compiled = []
    for pat, cap in patterns:
        compiled.append((re.compile(pat), cap))
    return compiled


def _text_to_manifest(text: str) -> list[dict]:
    """Scan plain text for dangerous API patterns; return a minimal capability manifest.

    Produces one record per matched capability type (de-duplicated to avoid inflating
    the reconciler's undisclosed-capability count). The file_path is synthetic so the
    intrinsic-risk pass (dynamic_code + obfuscation) can fire correctly.
    """
    global _TEXT_CAP_PATTERNS
    if not _TEXT_CAP_PATTERNS:
        _TEXT_CAP_PATTERNS = _build_text_patterns()

    found: dict[str, list[dict]] = {}
    for pattern, cap in _TEXT_CAP_PATTERNS:
        for m in pattern.finditer(text):
            if cap not in found:
                found[cap] = []
            found[cap].append({
                "capability_type": cap,
                "file_path": "<step_output>",
                "function_name": None,
                "line_start": text.count("\n", 0, m.start()) + 1,
                "evidence": {"api": m.group(0).strip()},
            })

    # Flatten; one record per capability type is enough for the reconciler passes.
    records: list[dict] = []
    for cap_records in found.values():
        records.append(cap_records[0])

    # Emit an obfuscated_input-tainted dynamic_code record when both dynamic_code and
    # obfuscation appear — this triggers _intrinsic_findings' decode-then-exec rule.
    if "dynamic_code" in found and "obfuscation" in found:
        tainted = dict(found["dynamic_code"][0])
        ev = dict(tainted.get("evidence") or {})
        ev["obfuscated_input"] = True
        tainted["evidence"] = ev
        records = [r for r in records if r["capability_type"] != "dynamic_code"]
        records.append(tainted)

    return records


def reconcile_mode_b(artifact_text: str, claimed_capabilities: list[str]) -> list[dict]:
    """Mode B reconciliation for runtime behavioral monitoring.

    Scans *artifact_text* (a step's output or generated code) for dangerous API
    patterns, builds a minimal capability manifest, then delegates to
    :func:`reconcile_blind` against the *claimed_capabilities* (role.tool_permissions).

    Intended for in-flight ACE instance monitoring — not a replacement for the
    full static-analysis path (:func:`assess_blind`).

    Args:
        artifact_text:       Plain text output from a running ACE step.
        claimed_capabilities: The role's declared tool_permissions (the allowed set).

    Returns:
        A list of integrity_findings-shaped dicts, same shape as :func:`reconcile_blind`.
        Empty list when no dangerous patterns are found or all are claimed.
    """
    manifest = _text_to_manifest(artifact_text)
    return reconcile_blind(manifest, set(claimed_capabilities))


# --------------------------------------------------------------------------- #
# Public reconciliation API
# --------------------------------------------------------------------------- #
def reconcile_blind(manifest: Any, claim: Any) -> list[dict]:
    """Reconcile an exercised capability manifest against a claimed set (Mode B).

    Args:
        manifest: the :func:`capability_extractor.extract` output (list of
            capability records), or a bare iterable of capability-type strings.
        claim: a :func:`claim_parser.parse_claim` result, a bare set/list of
            claimed capability-type strings, or ``None`` (nothing disclosed).

    Returns:
        A list of ``integrity_findings``-shaped dicts
        (``{source_scanner, finding_type, severity, file_path, line, detail}``):
        the undisclosed-capability gaps followed by the intrinsic-risk flags.
        Identical in shape to the scanner adapters' output, so the same
        ``_persist`` path writes them.
    """
    records = _normalize_manifest(manifest)
    claimed = _normalize_claim(claim)
    findings = _undisclosed_findings(records, claimed)
    findings.extend(_intrinsic_findings(records))
    return findings


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_findings
# --------------------------------------------------------------------------- #
def _persist(conn: Any, assessment_id: int, findings: list[dict]) -> list[int]:
    """Append every reconciliation finding to ``integrity_findings``; return ids."""
    tenant_id, classification, _ = _caller_context()
    ids: list[int] = []
    for f in findings:
        fid = _insert_finding(
            conn,
            (
                assessment_id,
                f["source_scanner"],
                f["finding_type"],
                f["severity"],
                f["file_path"],
                f["line"],
                json.dumps(f["detail"]),
                tenant_id,
                classification,
            ),
        )
        ids.append(fid)
    return ids


def _summarize(findings: list[dict], assessment_id: Optional[int], finding_ids: list[int]) -> dict:
    """Build the ``{by_severity, by_type, ...}`` rollup returned by the writers."""
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        by_type[f["finding_type"]] = by_type.get(f["finding_type"], 0) + 1
    return {
        "assessment_id": assessment_id,
        "findings": findings,
        "findings_persisted": len(finding_ids),
        "finding_ids": finding_ids,
        "by_severity": by_severity,
        "by_type": by_type,
    }


def reconcile_and_persist(
    assessment_id: int,
    manifest: Any,
    claim: Any,
    conn: Any = None,
) -> dict:
    """Reconcile (Mode B) and persist the findings append-only.

    Opens an RLS-aware connection when ``conn`` is ``None`` (closing it on exit);
    ``init_db`` runs idempotently so this works standalone. Returns the
    :func:`_summarize` rollup.
    """
    findings = reconcile_blind(manifest, claim)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        finding_ids = _persist(conn, assessment_id, findings)
    finally:
        if own_conn:
            conn.close()

    return _summarize(findings, assessment_id, finding_ids)


def assess_blind(
    path: str | os.PathLike,
    declared_purpose: Optional[str] = None,
    assessment_id: Optional[int] = None,
    conn: Any = None,
    llm_router: Any = None,
    llm_config: Optional[dict] = None,
) -> dict:
    """End-to-end Mode B assessment of an external artifact at ``path``.

    Extracts the exercised capability manifest, parses the claimed set from the
    artifact's own prose (+ an optional ``declared_purpose``), reconciles them,
    and — when ``assessment_id`` is given — persists the findings append-only.

    This is the primary provenance-blind entrypoint: no RTM / PRD required. The
    deterministic reconciliation always runs; when ``llm_assist.enabled`` an
    advisory LLM second opinion is attached under ``result['llm_advisory']``
    (``None`` otherwise — air-gap safe).
    """
    # Imported lazily so the pure reconcile path carries no extra import cost.
    from tools.integrity import capability_extractor, claim_parser

    manifest = capability_extractor.extract(path)
    claim = claim_parser.parse_claim(path, declared_purpose=declared_purpose)

    if assessment_id is None:
        findings = reconcile_blind(manifest, claim)
        result = _summarize(findings, None, [])
    else:
        result = reconcile_and_persist(assessment_id, manifest, claim, conn=conn)

    records = _normalize_manifest(manifest)
    claimed = sorted(_normalize_claim(claim))
    result["claimed_capabilities"] = claimed
    result["exercised_capabilities"] = sorted(
        {r["capability_type"] for r in records}
    )

    # Advisory only — deterministic findings above are the primary verdict.
    claim_text = "; ".join(
        part for part in (declared_purpose, "claimed: " + (", ".join(claimed) or "(none)")) if part
    )
    _attach_advisory(result, claim_text, records, config=llm_config, router=llm_router)
    return result


# --------------------------------------------------------------------------- #
# Mode A — provenance-aware reconciliation (RTM-derived ALLOWED capabilities)
# --------------------------------------------------------------------------- #
def _q(conn: Any, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    return sql.replace("?", "%s") if _backend_of(conn) == "postgresql" else sql


def _row_val(row: Any, key: str, idx: int) -> Any:
    """One column of a DB row, dict-cursor (aliased ``key``) or positional (``idx``)."""
    if row is None:
        return None
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    try:
        return row[idx]
    except (IndexError, KeyError, TypeError):
        return None


def _fetch_requirements(
    conn: Any, project_id: Optional[str], session_id: Optional[str]
) -> list[tuple[Any, str]]:
    """Read ``(id, raw_text)`` for a project's intake requirements.

    Optionally narrowed to a single ``session_id``. A missing ``intake_requirements``
    table (e.g. an environment that never ran intake) is tolerated: the query is
    wrapped so reconciliation degrades to "nothing authorized" rather than aborting
    the assessment.
    """
    sql = "SELECT id, raw_text FROM intake_requirements WHERE project_id = ?"
    params: list[Any] = [project_id]
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    try:
        rows = conn.execute(_q(conn, sql), tuple(params)).fetchall()
    except Exception as exc:  # noqa: BLE001 — missing table / schema variant -> no claims
        logger.warning(
            "reconcile_aware: cannot read intake_requirements for %s: %s", project_id, exc
        )
        return []
    out: list[tuple[Any, str]] = []
    for r in rows:
        out.append((_row_val(r, "id", 0), _row_val(r, "raw_text", 1) or ""))
    return out


def _allowed_capabilities(
    conn: Any,
    project_id: Optional[str],
    session_id: Optional[str],
    requirements: Optional[list[tuple[Any, str]]] = None,
) -> dict[str, list[dict]]:
    """Derive the ALLOWED-capability set from the project's requirement prose.

    Maps every requirement's ``raw_text`` through the *same* deterministic lexicon
    :func:`claim_parser.map_text` uses, so a requirement that says "notify by email"
    authorizes ``network_egress`` exactly as a README claim would. A capability is
    *allowed* iff at least one requirement implies it.

    Returns ``{capability_type: [{"requirement_id", "phrases"}]}`` — the authorizing
    requirement(s) per capability, retained as evidence for the HITL reviewer. The
    key set is the allowed-capability set; anything the code exercises outside it is
    the Mode A ``unauthorized_capability`` signal.

    ``requirements`` may be supplied pre-fetched (``(id, raw_text)`` tuples) to
    avoid re-reading ``intake_requirements``; otherwise they are read from ``conn``.
    """
    from tools.integrity import claim_parser

    if requirements is None:
        requirements = _fetch_requirements(conn, project_id, session_id)
    allowed: dict[str, list[dict]] = {}
    for req_id, text in requirements:
        for cap, phrases in claim_parser.map_text(text).items():
            allowed.setdefault(cap, []).append(
                {"requirement_id": req_id, "phrases": phrases}
            )
    return allowed


def _unauthorized_findings(records: list[dict], allowed: set[str]) -> list[dict]:
    """One ``unauthorized_capability`` finding per (file, capability) gap.

    The Mode A mirror of :func:`_undisclosed_findings`: a capability the code
    exercises that **no requirement authorizes** is the semantic-backdoor case.
    Records group by ``(file_path, capability_type)`` so multiple call sites in one
    file collapse to a single finding (anchored at the earliest line). Severity is
    the capability's inherent risk (``constants.RISK_WEIGHTS_CAPABILITY``).
    """
    groups: dict[tuple[Optional[str], str], list[dict]] = {}
    order: list[tuple[Optional[str], str]] = []
    for rec in records:
        cap = rec.get("capability_type")
        if not cap or cap in allowed:
            continue
        # Skip capabilities that are globally authorized for the platform
        # (platform_authorized_capabilities in integrity_config.yaml).
        if cap in _PLATFORM_AUTHORIZED_CAPABILITIES:
            continue
        # Skip filesystem findings for ICDEV's own first-party modules that are
        # authorized by known_safe_filesystem_modules in integrity_config.yaml.
        if cap == "filesystem" and _is_safe_filesystem_module(rec.get("file_path")):
            continue
        # Skip crypto findings for ICDEV's own first-party modules that are
        # authorized by known_safe_crypto_modules in integrity_config.yaml.
        if cap == "crypto" and _is_safe_crypto_module(rec.get("file_path")):
            continue
        # Skip obfuscation findings (base64 encoding for HTTP auth, binary data
        # transport, SAML, signing, etc.) for modules in known_safe_obfuscation_modules.
        if cap == "obfuscation" and _is_safe_obfuscation_module(rec.get("file_path")):
            continue
        # Skip serialization findings for modules that only pickle internal-only
        # artifacts (e.g. ML models trained and loaded within the platform).
        if cap == "serialization" and _is_safe_serialization_module(rec.get("file_path")):
            continue
        key = (rec.get("file_path"), cap)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    findings: list[dict] = []
    allowed_sorted = sorted(allowed)
    for key in order:
        file_path, cap = key
        group = groups[key]
        weight = RISK_WEIGHTS_CAPABILITY.get(cap, 0.0)
        findings.append(
            {
                "source_scanner": SOURCE_SCANNER,
                "finding_type": "unauthorized_capability",
                "severity": _severity_for_weight(weight),
                "file_path": file_path,
                "line": _earliest_line(group),
                "detail": {
                    "capability_type": cap,
                    "reason": (
                        f"code exercises '{cap}' but no intake requirement (RTM) "
                        f"authorizes it — the semantic-backdoor case"
                    ),
                    "risk_weight": weight,
                    "occurrences": len(group),
                    "allowed_capabilities": allowed_sorted,
                    "sites": [_site(r) for r in group[:_MAX_SITES]],
                },
            }
        )
    return findings


def _coverage_gaps(
    project_id: Optional[str],
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Requirements with **no implementing code**, via the full RTM.

    Builds the Requirements Traceability Matrix
    (:func:`tools.requirements.traceability_builder.build_rtm`, which also persists
    ``review_traceability``) and keeps the gaps where the ``code_modules`` trace
    dimension is missing — a requirement the delivered code never implements (the
    inverse of an unauthorized capability). Best-effort: a missing project / RTM
    table / DB never aborts reconciliation, it just yields no coverage gaps.
    """
    try:
        from tools.requirements.traceability_builder import build_rtm

        rtm = build_rtm(project_id, session_id=session_id, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 — RTM is advisory; never abort the assessment
        logger.warning(
            "reconcile_aware: RTM build unavailable for %s: %s", project_id, exc
        )
        return []
    gaps: list[dict] = []
    for gap in rtm.get("gaps", []):
        if "code_modules" in (gap.get("missing_links") or []):
            gaps.append(
                {
                    "requirement_id": gap.get("requirement_id"),
                    "requirement_text": gap.get("requirement_text"),
                    "missing_links": gap.get("missing_links"),
                    "severity": gap.get("severity"),
                    "coverage_pct": gap.get("coverage_pct"),
                }
            )
    return gaps


def reconcile_aware(
    manifest: Any,
    project_id: Optional[str],
    session_id: Optional[str] = None,
    conn: Any = None,
    db_path: Optional[str] = None,
    llm_router: Any = None,
    llm_config: Optional[dict] = None,
) -> dict:
    """Reconcile an exercised manifest against the RTM-derived ALLOWED set (Mode A).

    Derives the allowed-capability set from the project's ``intake_requirements``
    prose (mapped through the shared ``claim_parser`` lexicon), flags every
    exercised capability with no authorizing requirement as
    ``unauthorized_capability``, runs the intrinsic-risk pass (dangerous regardless
    of authorization), and surfaces requirements with no implementing code as
    coverage gaps.

    Args:
        manifest: the :func:`capability_extractor.extract` output (list of
            capability records), or a bare iterable of capability-type strings.
        project_id: the provenance handle whose RTM / requirements authorize the
            capability set.
        session_id: optional intake-session narrowing of the requirement scope.
        conn: optional existing RLS-aware connection to reuse (engine / tests);
            when ``None`` one is opened and closed internally for the requirement
            read.
        db_path: optional DB path forwarded to ``build_rtm`` for the coverage-gap
            pass (defaults to the configured ICDEV DB).

    Returns:
        ``{"mode", "project_id", "session_id", "findings", "allowed_capabilities",
        "exercised_capabilities", "coverage_gaps"}``. ``findings`` are the
        ``integrity_findings``-shaped ``unauthorized_capability`` + intrinsic-risk
        dicts — identical in shape to the scanner adapters' output.
    """
    records = _normalize_manifest(manifest)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        requirements = _fetch_requirements(conn, project_id, session_id)
        allowed_map = _allowed_capabilities(
            conn, project_id, session_id, requirements=requirements
        )
    finally:
        if own_conn:
            conn.close()

    findings = _unauthorized_findings(records, set(allowed_map))
    findings.extend(_intrinsic_findings(records))
    coverage_gaps = _coverage_gaps(project_id, session_id, db_path)

    result = {
        "mode": "provenance_aware",
        "project_id": project_id,
        "session_id": session_id,
        "findings": findings,
        "allowed_capabilities": {cap: allowed_map[cap] for cap in sorted(allowed_map)},
        "exercised_capabilities": sorted(
            {r["capability_type"] for r in records if r.get("capability_type")}
        ),
        "coverage_gaps": coverage_gaps,
    }

    # Advisory only — the unauthorized/intrinsic findings above are the primary
    # verdict. The requirement prose is the text the manifest is judged against.
    requirement_text = "\n".join(text for _, text in requirements if text)
    _attach_advisory(result, requirement_text, records, config=llm_config, router=llm_router)
    return result


def reconcile_aware_and_persist(
    assessment_id: int,
    manifest: Any,
    project_id: Optional[str],
    session_id: Optional[str] = None,
    conn: Any = None,
    db_path: Optional[str] = None,
    llm_router: Any = None,
    llm_config: Optional[dict] = None,
) -> dict:
    """Reconcile (Mode A) and persist the findings append-only.

    Opens an RLS-aware connection when ``conn`` is ``None`` (closing it on exit);
    ``init_db`` runs idempotently. Returns the :func:`_summarize` rollup extended
    with ``coverage_gaps`` / ``allowed_capabilities`` / ``exercised_capabilities``
    / ``mode`` / ``llm_advisory`` so the engine + route surface the full Mode A
    disposition.
    """
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        result = reconcile_aware(
            manifest,
            project_id,
            session_id,
            conn=conn,
            db_path=db_path,
            llm_router=llm_router,
            llm_config=llm_config,
        )
        finding_ids = _persist(conn, assessment_id, result["findings"])
    finally:
        if own_conn:
            conn.close()

    summary = _summarize(result["findings"], assessment_id, finding_ids)
    summary["mode"] = "provenance_aware"
    summary["coverage_gaps"] = result["coverage_gaps"]
    summary["allowed_capabilities"] = result["allowed_capabilities"]
    summary["exercised_capabilities"] = result["exercised_capabilities"]
    summary["llm_advisory"] = result.get("llm_advisory")
    return summary


def assess_aware(
    path: str | os.PathLike,
    project_id: Optional[str],
    session_id: Optional[str] = None,
    assessment_id: Optional[int] = None,
    conn: Any = None,
    db_path: Optional[str] = None,
    llm_router: Any = None,
    llm_config: Optional[dict] = None,
) -> dict:
    """End-to-end Mode A assessment of an artifact at ``path``.

    Extracts the exercised capability manifest, reconciles it against the
    RTM-derived ALLOWED set for ``project_id`` (+ optional ``session_id``), and —
    when ``assessment_id`` is given — persists the findings append-only.

    This is the provenance-aware entrypoint: an RTM / intake requirements drive the
    authorization, unlike the claim-based :func:`assess_blind`. The deterministic
    reconciliation always runs; an advisory LLM second opinion is attached under
    ``result['llm_advisory']`` when ``llm_assist.enabled`` (``None`` otherwise).
    """
    # Imported lazily so the pure reconcile path carries no extra import cost.
    from tools.integrity import capability_extractor

    manifest = capability_extractor.extract(path)

    if assessment_id is None:
        return reconcile_aware(
            manifest,
            project_id,
            session_id,
            conn=conn,
            db_path=db_path,
            llm_router=llm_router,
            llm_config=llm_config,
        )
    # The persist path's _summarize rollup already carries the findings list +
    # coverage_gaps / allowed / exercised — no need to reconcile a second time.
    return reconcile_aware_and_persist(
        assessment_id,
        manifest,
        project_id,
        session_id,
        conn=conn,
        db_path=db_path,
        llm_router=llm_router,
        llm_config=llm_config,
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA intent reconciler (Mode B) — reconcile the exercised "
        "capability manifest against the author's claim; emit undisclosed_capability "
        "+ intrinsic-risk integrity_findings. The primary external-code path (no PRD).",
    )
    parser.add_argument("--path", required=True, help="file or directory to assess")
    parser.add_argument(
        "--declared-purpose",
        default=None,
        help="free-text purpose claim to fold into the claimed set",
    )
    parser.add_argument(
        "--assessment-id",
        type=int,
        default=None,
        help="integrity_assessments.id to attach + persist findings to "
        "(omit to print the reconciliation without persisting)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = assess_blind(
        args.path,
        declared_purpose=args.declared_purpose,
        assessment_id=args.assessment_id,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"SIPA Mode B reconciliation — {args.path}")
    print(f"  claimed:   {', '.join(result['claimed_capabilities']) or '(none)'}")
    print(f"  exercised: {', '.join(result['exercised_capabilities']) or '(none)'}")
    for f in result["findings"]:
        loc = f"{f['file_path']}:{f['line']}" if f.get("file_path") else "(no file)"
        cap = f["detail"].get("capability_type") or f["detail"].get("rule", "")
        print(f"  [{f['severity']:>8}] {f['finding_type']}({cap}) — {loc}")
    if args.assessment_id is not None:
        print(f"  persisted: {result['findings_persisted']} finding(s)")
    else:
        print(f"  total: {len(result['findings'])} finding(s) (not persisted)")


if __name__ == "__main__":
    main()
