# CUI // SP-CTI
"""AI-ify from OBSERVED behaviour: the no-source input mode (rea-aiify-01, card ``rea``).

WHY. Every other AI-ify path reads SOURCE (Semgrep / AST in
``pattern_classifier``). A website, a recorded HAR, an Electron app or an APK
the operator holds no source for had no way in. This module turns what REA
OBSERVED about such an artifact -- the endpoints a HAR exercised, the routes /
IPC channels / traced features of a JS application -- into a feature
inventory, and each inventory feature that names an AI-Ify pattern into an
opportunity the EXISTING ``opportunity_scorer`` scores and the EXISTING
``roadmap_generator`` places.

    input_type     artifact                          REA family (rea_adapter)
    har            local .har                        har
    electron_app   app directory or .asar            js_bundle
    website        local saved site (dir / .html)    js_bundle
                   http(s):// URL                    website  (live; see below)
    apk            local .apk                        apk

REA is reached ONLY through ``tools.analyzers.dispatch`` -- so the analyzer
contract's ``sandbox: sandboxed`` posture, rate limit and status lifting apply
-- and never by spawning anything here.

BASIS. Every opportunity carries ``basis: observed`` (on the pattern and in
``pattern_detail``), a ``module_path`` under ``observed:`` and line 0, so it is
never confused with a Semgrep code-pattern hit. ``language`` is the column's
nearest legal value (``typescript``) and ``pattern_detail.language_basis``
says it was not measured.

HONESTY RULES (house rules, applied):
  * an inventory nobody measured is ``None`` with ``features_basis`` naming
    why -- never ``[]``. ``[]`` means REA answered and named nothing.
  * a feature that matches no AI-Ify pattern stays in the inventory
    (``unmapped``) and is NOT scored -- no opportunity is invented for a route
    just because it exists.
  * URLs are reduced to ``METHOD /path`` with id-like segments collapsed; the
    query string, fragment and host never leave this module.

RAILS, refused BEFORE a scan row exists (:class:`ObservedInputRefused`):
  * no authorization basis (``owned`` | ``licensed`` | ``customer-authorized``,
    from ``scan_context['authorization_basis']`` or
    ``ICDEV_RE_AUTHORIZATION_BASIS``);
  * a local artifact that does not exist (analysis is local; nothing uploads);
  * a HAR with no ``sensitive_values`` (``[]`` asserts none) -- REA keeps
    response-body secrets verbatim (rea-spike-01);
  * a live website URL without ``allow_execute: true``. With it, the run still
    reports REA's ``website`` family honestly (``unavailable`` /
    ``not_allowlisted`` in REA 6.4.0) -- no live capture happens here.

Public API:
    OBSERVED_INPUT_TYPES, is_observed_input(input_type)
    check_observed_input(input_type, input_ref, scan_context) -> dict
    build_inventory(input_type, input_ref, scan_context, *, dispatcher=None) -> dict
    features_to_patterns(inventory) -> list[dict]
"""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

BASIS_OBSERVED = "observed"
AUTHORIZATION_BASES: Tuple[str, ...] = ("owned", "licensed", "customer-authorized")
ENV_AUTHORIZATION = "ICDEV_RE_AUTHORIZATION_BASIS"

#: input_type -> what it is. ``family`` None means "decided from the ref".
OBSERVED_INPUT_TYPES: Dict[str, str] = {
    "har": "Recorded HTTP archive of a web session (local .har)",
    "electron_app": "Electron / JS desktop app (directory or .asar), analysed without executing it",
    "website": "Website: a local saved copy, or an http(s) URL (live -- needs allow_execute)",
    "apk": "Android package (local .apk)",
}

#: dispatch observable type -> contract declaration key.
_DECLARATION = {"har": "rea_har", "js_bundle": "rea_javascript", "website": "rea_website",
                "apk": "rea_apk"}

#: The ``language`` column is a CHECK over source languages; observed web /
#: Electron behaviour is closest to this one, and the detail says so.
OBSERVED_LANGUAGE = "typescript"
LANGUAGE_BASIS = "not_measured: observed behaviour, no source language; column requires one"

#: Feature text -> AI-Ify pattern (``context/aiify/pattern_catalog.json`` ids),
#: first match wins. A feature none of these names is ``unmapped``.
_PATTERN_RULES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (ptype, re.compile(rx, re.IGNORECASE)) for ptype, rx in (
        ("document_ingestion_pipeline", r"upload|import|ingest|attach|intake"),
        ("ocr_extraction_pipeline", r"\bocr\b|scan(ned)?[-_ ]?(doc|image|page)"),
        ("fulltext_search_engine", r"search|query|lookup|\bfind\b|autocomplete|suggest"),
        ("manual_classification_ui", r"classif|categor|\btags?\b|label|triage"),
        ("document_routing", r"route[-_ ]?to|dispatch|assign|approv|workflow|escalat"),
        ("scheduled_cron", r"schedul|\bcron\b|\bpoll|interval|\bsync\b"),
        ("string_template_rendering", r"report|export|render|template|email|notif|letter|print"),
        ("metadata_extraction", r"\bform\b|metadata|extract|\bparse\b|invoice|receipt"),
    )
)

_STATIC_ASSET_RE = re.compile(
    r"\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|otf|mp4|webm)$", re.IGNORECASE)
_ID_SEGMENT_RE = re.compile(
    r"^(\d+|[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class ObservedInputRefused(RuntimeError):
    """A rail refused the input before anything ran. ``reason`` is machine-readable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason


def is_observed_input(input_type: Optional[str]) -> bool:
    return (input_type or "") in OBSERVED_INPUT_TYPES


def _is_url(ref: str) -> bool:
    return urllib.parse.urlparse(ref).scheme.lower() in ("http", "https")


def _family(input_type: str, ref: str) -> str:
    """The rea_adapter family this input is analysed as."""
    if input_type == "har":
        return "har"
    if input_type == "apk":
        return "apk"
    if input_type == "website":
        if _is_url(ref):
            return "website"
        return "har" if ref.lower().endswith(".har") else "js_bundle"
    return "js_bundle"  # electron_app


def _authorization(scan_context: Mapping[str, Any]) -> Optional[str]:
    raw = scan_context.get("authorization_basis")
    if raw is None:
        raw = os.environ.get(ENV_AUTHORIZATION, "")
    basis = str(raw or "").strip().lower()
    return basis if basis in AUTHORIZATION_BASES else None


def _digest(path: Path) -> Optional[str]:
    try:
        from icdev.tools.mcp_client import target_auth

        return target_auth.target_digest(path)
    except Exception:  # noqa: BLE001 -- recorded as None, the rails still apply
        return None


def check_observed_input(input_type: str, input_ref: str,
                         scan_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Apply every rail; return the resolved run plan, or raise :class:`ObservedInputRefused`."""
    scan_context = dict(scan_context or {})
    if not is_observed_input(input_type):
        raise ObservedInputRefused("not_observed_input", f"{input_type!r} is not an observed input type")
    ref = (input_ref or "").strip()
    if not ref:
        raise ObservedInputRefused("empty_input", "empty input reference")
    family = _family(input_type, ref)
    allow_execute = scan_context.get("allow_execute") is True
    basis = _authorization(scan_context)

    target: str
    sha256: Optional[str] = None
    if family == "website":
        if not allow_execute:
            raise ObservedInputRefused(
                "allow_execute_required",
                "a live website is captured by visiting it; pass allow_execute=true for this run "
                "(or save the site locally / record a HAR and pass that). Nothing was run.")
        target = ref
    else:
        if _is_url(ref):
            raise ObservedInputRefused(
                "local_artifact_required",
                f"{input_type} input must be a local file or directory, not a URL: analysis stays "
                "on this machine and nothing is downloaded. Nothing was run.")
        path = Path(ref).expanduser()
        if not path.exists():
            raise ObservedInputRefused("source_unreadable", f"{ref!r} does not exist. Nothing was run.")
        target = str(path.resolve())
        sha256 = _digest(Path(target))

    if basis is None:
        raise ObservedInputRefused(
            "authorization_missing",
            "no authorization basis for this target (%s): pass scan_context['authorization_basis'] "
            "or set %s. Nothing was run." % (" | ".join(AUTHORIZATION_BASES), ENV_AUTHORIZATION))

    sensitive = scan_context.get("sensitive_values")
    if family == "har" and sensitive is None:
        raise ObservedInputRefused(
            "sensitive_values_required",
            "REA keeps response-BODY secrets verbatim in HAR evidence (rea-spike-01). Pass "
            "scan_context['sensitive_values'] -- [] to assert there are none. Nothing was run.")

    return {
        "input_type": input_type,
        "family": family,
        "target": target,
        "provenance": {"target_sha256": sha256, "authorization_basis": basis,
                       "allow_execute": allow_execute, "analysis": "local"},
        "context": {"authorization_basis": basis, "actor": scan_context.get("actor"),
                    "sensitive_values": list(sensitive) if sensitive is not None else None,
                    "feature_seeds": _seeds(scan_context.get("feature_seeds"))},
    }


def _seeds(raw: Any) -> Optional[List[Dict[str, str]]]:
    """``feature_seeds``: strings (a ``string`` seed) or ``{kind, value}`` dicts."""
    if not raw:
        return None
    out: List[Dict[str, str]] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append({"kind": "string", "value": item.strip()})
        elif isinstance(item, Mapping) and str(item.get("value") or "").strip():
            out.append({"kind": str(item.get("kind") or "string"), "value": str(item["value"]).strip()})
    return out or None


# ---------------------------------------------------------------------------
# REA evidence -> feature inventory
# ---------------------------------------------------------------------------

def _route_label(method: Optional[str], url: str) -> Optional[str]:
    """``METHOD /path`` with id-like segments collapsed; None for a static asset."""
    path = urllib.parse.urlparse(url).path if "://" in url else url.split("?", 1)[0].split("#", 1)[0]
    path = path or "/"
    if _STATIC_ASSET_RE.search(path):
        return None
    segments = ["{id}" if _ID_SEGMENT_RE.match(seg) else seg for seg in path.split("/")]
    label = "/".join(segments) or "/"
    method = (method or "").upper()
    return f"{method} {label}" if method in _METHODS else label


def _walk(value: Any, depth: int = 0) -> Iterable[Dict[str, Any]]:
    if depth > 10:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child, depth + 1)


def _candidates(normalized: Any) -> Iterable[Tuple[str, str]]:
    """``(kind, label)`` for every named route / endpoint / IPC channel in a result."""
    for node in _walk(normalized):
        method = node.get("method")
        for key in ("url", "route", "path"):
            target = node.get(key)
            if isinstance(target, str) and target and (method or key == "route"):
                label = _route_label(method if isinstance(method, str) else None, target)
                if label:
                    yield ("http_endpoint" if method else "route"), label
                break
        channel = node.get("channel")
        if isinstance(channel, str) and channel.strip():
            yield "ipc_channel", channel.strip()


def _trace_feature(entry: Mapping[str, Any]) -> Optional[Tuple[str, int]]:
    """A ``trace_application_feature`` record -> ``(label, reach)``, or None if it matched nothing."""
    normalized = entry.get("normalized_result")
    if not isinstance(normalized, dict):
        return None
    seed = normalized.get("seed")
    label = seed.get("value") if isinstance(seed, dict) else None
    lists = [v for v in normalized.values() if isinstance(v, list)]
    reach = sum(len(v) for v in lists)
    if lists and reach == 0:
        return None  # REA answered: this seed reaches nothing
    return (str(label) if label else f"traced feature {str(entry.get('evidence_id'))[:15]}"), reach


def _classify(label: str) -> Optional[str]:
    for ptype, rx in _PATTERN_RULES:
        if rx.search(label):
            return ptype
    return None


def extract_features(body: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Every feature REA's evidence names, deduplicated, with its evidence ids."""
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add(kind: str, label: str, entry: Mapping[str, Any], weight: int = 1) -> None:
        key = (kind, label)
        feature = found.setdefault(key, {
            "kind": kind, "label": label, "occurrences": 0, "evidence_ids": [],
            "tools": [], "rea_confidence": entry.get("rea_confidence") or entry.get("confidence"),
        })
        feature["occurrences"] += weight
        ev = entry.get("evidence_id")
        if ev and ev not in feature["evidence_ids"]:
            feature["evidence_ids"].append(ev)
        if entry.get("tool") not in feature["tools"]:
            feature["tools"].append(entry.get("tool"))

    for entry in body:
        if entry.get("tool") == "trace_application_feature":
            traced = _trace_feature(entry)
            if traced:
                add("traced_feature", traced[0], entry, max(1, traced[1]))
            continue
        for kind, label in _candidates(entry.get("normalized_result")):
            add(kind, label, entry)
    features = sorted(found.values(), key=lambda f: (-f["occurrences"], f["kind"], f["label"]))
    for feature in features:
        feature["pattern_type"] = _classify(feature["label"])
    return features


def _surface_counts(body: List[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """REA's JS summary counts (Electron / IPC / statistics) -- unnamed, reported as-is."""
    for entry in body:
        normalized = entry.get("normalized_result")
        summary = normalized.get("summary") if isinstance(normalized, dict) else None
        if isinstance(summary, dict):
            return {k: summary.get(k) for k in ("statistics", "electron") if k in summary}
    return None


def build_inventory(input_type: str, input_ref: str,
                    scan_context: Optional[Mapping[str, Any]] = None, *,
                    dispatcher: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Run REA (through the analyzer dispatcher) and return the feature inventory.

    Raises :class:`ObservedInputRefused` when a rail refuses; otherwise always
    returns a dict whose ``features`` is ``None`` (not measured, with
    ``features_basis`` saying why) or a list (measured).
    """
    plan = check_observed_input(input_type, input_ref, scan_context)
    if dispatcher is None:
        from tools.analyzers.dispatch import dispatch as dispatcher  # noqa: PLC0415

    family = plan["family"]
    context = {k: v for k, v in plan["context"].items() if v is not None}
    result = dispatcher(family, plan["target"], context=context,
                        analyzers=[_DECLARATION[family]])
    reports = list(getattr(result, "reports", ()) or ())
    report = reports[0] if reports else None
    data = getattr(report, "data", None) if report is not None else None
    data = data if isinstance(data, dict) else {}
    status = getattr(report, "status", None) or "unavailable"
    reason = data.get("reason") or (None if status in ("ok", "partial") else status)
    detail = data.get("reason_detail") or (getattr(report, "detail", None) if report else
                                           "no analyzer accepted this input")
    body = list(data.get("body") or [])

    inventory: Dict[str, Any] = {
        "basis": BASIS_OBSERVED,
        "input_type": input_type,
        "family": family,
        "analyzer": _DECLARATION[family],
        "status": status,
        "reason": reason,
        "reason_detail": detail,
        "execution_mode": getattr(report, "execution_mode", None) if report else None,
        "provenance": {**plan["provenance"], **{k: v for k, v in (data.get("provenance") or {}).items()
                                                  if v is not None}},
        "evidence_ids": data.get("evidence_ids"),
        "limitations": data.get("limitations"),
        "residual_unknowns": data.get("residual_unknowns"),
        "surface_counts": _surface_counts(body),
        "features": None,
        "features_basis": None,
        "unmapped": None,
    }
    if status not in ("ok", "partial"):
        inventory["features_basis"] = f"not_run: {reason}"
        return inventory
    if not body:
        # e.g. an over-budget answer: the analysis completed, its evidence is
        # retained in REA, but nothing arrived inline to read features from.
        inventory["features_basis"] = f"no_evidence_inline: {reason or 'REA returned no record'}"
        return inventory
    features = extract_features(body)
    if not features and all(e.get("tool") == "analyze_javascript_application" for e in body):
        # A JS summary is counts only: it NAMES no route or channel, so it
        # cannot say "none". Seeds (feature_seeds) are what name features.
        inventory["features_basis"] = ("not_enumerated: REA's summary view names no routes or "
                                       "channels; pass feature_seeds to trace named features")
        return inventory
    inventory["features"] = features
    inventory["features_basis"] = "observed_by_rea" + ("" if status == "ok" else f" ({reason})")
    inventory["unmapped"] = [f["label"] for f in features if f["pattern_type"] is None]
    return inventory


def features_to_patterns(inventory: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Mapped inventory features -> pattern dicts for ``score_and_assess``.

    Unmapped features are not scored. For a HAR, ``usage_freq`` is the
    feature's OBSERVED share of exchanges (the busiest endpoint is 1.0), a
    measured signal the scorer otherwise derives from a path heuristic.
    """
    features = inventory.get("features") or []
    mapped = [f for f in features if f.get("pattern_type")]
    if not mapped:
        return []
    top = max(f["occurrences"] for f in mapped) or 1
    prov = inventory.get("provenance") or {}
    patterns: List[Dict[str, Any]] = []
    for f in mapped:
        pattern: Dict[str, Any] = {
            "module_path": f"observed:{inventory.get('input_type')}:{f['label']}",
            "function_name": f["label"],
            "line_start": 0,
            "line_end": 0,
            "language": OBSERVED_LANGUAGE,
            "pattern_type": f["pattern_type"],
            "basis": BASIS_OBSERVED,
            "pattern_detail": {
                "basis": BASIS_OBSERVED,
                "source": "rea",
                "input_type": inventory.get("input_type"),
                "family": inventory.get("family"),
                "feature_kind": f["kind"],
                "feature": f["label"],
                "occurrences": f["occurrences"],
                "evidence_ids": list(f["evidence_ids"]),
                "rea_confidence": f.get("rea_confidence"),
                "target_sha256": prov.get("target_sha256"),
                "authorization_basis": prov.get("authorization_basis"),
                "language_basis": LANGUAGE_BASIS,
            },
        }
        if inventory.get("family") == "har":
            pattern["usage_freq"] = round(0.3 + 0.7 * f["occurrences"] / top, 4)
        patterns.append(pattern)
    return patterns
