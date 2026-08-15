# CUI // SP-CTI
"""One contract validator for LLM output (trust-struct-01).

Before this module every surface that asked an LLM for JSON re-implemented the
same three steps inline — ``re.search(r"\\{.*\\}")``, ``json.loads``, then a
hand-written ``isinstance`` check per field — and each one degraded differently
when the model returned something else. Three copies existed
(``content_grounding._ground_content_llm``, ``constitutional_ai.critique_rule``,
``reflective_reranker.reflect_document``) and NOTHING in the repo validated an
LLM payload against a declared shape.

  - OutputContract(schema)                  -> a declared, self-checked shape
  - validate_against_contract(obj, c)       -> findings[] (pure, no parsing)
  - coerce_or_reject(raw_text, c)           -> (obj|None, findings)
  - repair_once(raw_text, c, router=...)    -> (obj|None, findings), ONE retry

Design rules, each one a failure this platform has already shipped:

1. **Unparseable input fails CLOSED.** ``coerce_or_reject`` returns ``None``
   plus an ``unparseable`` finding; it never returns a half-populated dict for
   a caller to mistake for a judgement. Whether that refusal means "block" or
   "fall back to the deterministic floor" is the CALLER's policy, stated at the
   call site — this module only ever says conforms / does not conform.

2. **A repair happens only where the contract DECLARED the fallback.** Enum
   fields carry ``fail_closed``, the sentinel an unknown token maps to — exactly
   what ``categorical_scoring.map_grounding_enum`` does when it treats an
   out-of-vocabulary label as ``ungrounded``, and what
   ``content_grounding._ground_content_llm`` does when it pads a short label
   list. There is no generic type-coercion ladder ("3" -> 3, "yes" -> True):
   a guessed value is indistinguishable from a judged one downstream.

3. **``coerce`` never yields a PARTLY valid object.** If any violation cannot be
   repaired from a declared sentinel, the whole payload is rejected. A dict that
   satisfied four fields and silently dropped the fifth is the shape of bug this
   module exists to remove.

4. **An unrecognised schema keyword raises at CONSTRUCTION time.** Silently
   ignoring ``minLength`` would make the validator report conformance it never
   checked — declared-but-unconsumed validation, in miniature. The supported
   subset is small and closed (see ``SUPPORTED_KEYWORDS``); annotation-only keys
   (``description``, ``title``, ...) are ignored BY DECLARATION.

NO new dependency. This is a JSON-schema subset in plain Python — stdlib ``json``
and ``re`` only — matching the stdlib-first posture of ``citation_grounding.py``.
It is deliberately NOT ``jsonschema``: the optional-import pattern in
``tools/cortex/api.py::_validate_against_schema`` returns ``(True, "")`` when the
package is absent, i.e. an air-gapped deployment validates nothing and reports
that everything conformed.

Scope note: ``tools/cortex/api.py::extract`` keeps its own jsonschema check and
its documented DEGRADE-not-refuse contract (a non-conforming payload sets
``schema_valid=False`` in-band). It accepts caller-supplied arbitrary JSON
schemas, which the closed subset here would reject at construction; converting
that surface is a separate, behaviour-changing decision.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

# ── The supported subset ─────────────────────────────────────────────────────

#: Keywords this validator actually enforces. Anything outside this set (and
#: ANNOTATION_KEYWORDS) raises ContractError when the contract is built, rather
#: than being ignored at validation time.
SUPPORTED_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "fail_closed",
    }
)

#: Keys carried for humans/providers and ignored here by declaration, not by
#: oversight. ``$schema``/``title``/``description`` travel with schemas that are
#: also handed to a provider's native structured-output mode.
ANNOTATION_KEYWORDS = frozenset({"$schema", "title", "description", "examples", "default"})

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # bool is an int subclass in Python; a boolean is never a number here.
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}

# Finding codes. Stable strings — callers log/branch on them.
UNPARSEABLE = "unparseable"
EMPTY_OUTPUT = "empty_output"
TYPE_MISMATCH = "type_mismatch"
MISSING_REQUIRED = "missing_required"
ENUM_VIOLATION = "enum_violation"
ADDITIONAL_PROPERTY = "additional_property"
TOO_FEW_ITEMS = "too_few_items"
TOO_MANY_ITEMS = "too_many_items"
REPAIR_UNAVAILABLE = "repair_unavailable"
REPAIR_FAILED = "repair_failed"


class ContractError(ValueError):
    """A contract's own schema is malformed or uses an unsupported keyword.

    Subclasses ValueError: this is a programming error in the CALLER's declared
    contract, not a runtime judgement about an LLM's output.
    """


def _finding(code: str, path: str, message: str, **extra) -> dict:
    f = {"code": code, "path": path, "message": message, "repaired": False}
    f.update(extra)
    return f


# ── Contract ─────────────────────────────────────────────────────────────────


def enum_field(values, *, fail_closed: Optional[str] = None, description: str = "") -> dict:
    """Build one string-enum property node.

    ``fail_closed`` is the sentinel an unknown token maps to under
    ``mode="coerce"``. Omit it and an unknown token is simply a rejection — that
    is the stricter posture, so it is the default.
    """
    node: dict = {"type": "string", "enum": [str(v) for v in values]}
    if fail_closed is not None:
        node["fail_closed"] = str(fail_closed)
    if description:
        node["description"] = description
    return node


@dataclass(frozen=True)
class OutputContract:
    """A declared shape for one LLM call's output.

    ``schema`` is a JSON-schema SUBSET (see module docstring). It is walked and
    checked at construction, so a typo in a contract surfaces at import/def time
    rather than as a validation that silently passes everything.

    ``name`` is carried into audit/log lines so a finding can be attributed to
    the surface that declared it.
    """

    schema: dict
    name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.schema, dict):
            raise ContractError("contract schema must be a dict")
        _check_schema(self.schema, "$")

    @property
    def root_type(self) -> str:
        """The declared root type, or "" when the root only declares an enum."""
        return str(self.schema.get("type", ""))

    def prompt_fragment(self) -> str:
        """The schema as compact JSON, for inlining in the request prompt.

        ``fail_closed`` is stripped: it is OUR fallback policy, not an
        instruction to the model, and showing it invites the model to emit the
        sentinel instead of judging.
        """
        return json.dumps(_strip_policy_keys(self.schema), ensure_ascii=False)


def _check_schema(node: Any, path: str) -> None:
    """Recursively verify a contract uses only the supported subset."""
    if not isinstance(node, dict):
        raise ContractError(f"{path}: schema node must be a dict, got {type(node).__name__}")
    unknown = set(node) - SUPPORTED_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        raise ContractError(
            f"{path}: unsupported schema keyword(s) {sorted(unknown)}; "
            f"supported subset is {sorted(SUPPORTED_KEYWORDS)}"
        )
    declared = node.get("type")
    if declared is not None:
        if not isinstance(declared, str):
            raise ContractError(f"{path}: 'type' must be a single type name string")
        if declared not in _TYPE_CHECKS:
            raise ContractError(f"{path}: unknown type {declared!r}")
    if "enum" in node:
        if not isinstance(node["enum"], list) or not node["enum"]:
            raise ContractError(f"{path}: 'enum' must be a non-empty list")
        if "fail_closed" in node and node["fail_closed"] not in node["enum"]:
            raise ContractError(
                f"{path}: fail_closed {node['fail_closed']!r} is not one of the declared enum values"
            )
    props = node.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            raise ContractError(f"{path}: 'properties' must be a dict")
        for key, sub in props.items():
            _check_schema(sub, f"{path}.{key}")
    required = node.get("required")
    if required is not None:
        if not isinstance(required, (list, tuple)) or any(not isinstance(r, str) for r in required):
            raise ContractError(f"{path}: 'required' must be a list of property names")
        if props is not None:
            missing = [r for r in required if r not in props]
            if missing:
                raise ContractError(f"{path}: required names {missing} have no declared property")
    items = node.get("items")
    if items is not None:
        _check_schema(items, f"{path}[]")
    for bound in ("minItems", "maxItems"):
        if bound in node and not isinstance(node[bound], int):
            raise ContractError(f"{path}: '{bound}' must be an int")


def _strip_policy_keys(node: Any) -> Any:
    """Copy a schema without ICDEV-only policy keys (``fail_closed``)."""
    if not isinstance(node, dict):
        return node
    out = {k: v for k, v in node.items() if k != "fail_closed"}
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _strip_policy_keys(v) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _strip_policy_keys(out["items"])
    return out


# ── Validation ───────────────────────────────────────────────────────────────


def validate_against_contract(obj: Any, contract: OutputContract) -> list[dict]:
    """Return every way ``obj`` violates ``contract`` (empty list == conforms).

    Pure: no parsing, no repair, no I/O. Each finding is
    ``{code, path, message, repaired}`` where ``path`` is a JSON-pointer-ish
    ``$.labels[2]`` locator and ``code`` is one of the module constants.
    """
    findings: list[dict] = []
    _walk(obj, contract.schema, "$", findings, repair=False)
    return findings


def _normalize_enum_token(value: Any, allowed: list) -> Optional[str]:
    """Return the declared enum value ``value`` matches after strip+lower, else None.

    Only string-vs-string; a numeric or structured enum is matched exactly.
    """
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    for declared in allowed:
        if isinstance(declared, str) and declared.strip().lower() == token:
            return declared
    return None


def _walk(value: Any, schema: dict, path: str, findings: list[dict], *, repair: bool):
    """Validate one node. Returns ``(value, ok)``.

    Under ``repair`` a violation is replaced by the node's declared
    ``fail_closed`` sentinel and the finding is marked ``repaired``. Without a
    declared sentinel the violation stands and ``ok`` is False — there is no
    guessing.
    """
    sentinel_declared = "fail_closed" in schema

    def _fail(code: str, message: str):
        finding = _finding(code, path, message)
        if repair and sentinel_declared:
            finding["repaired"] = True
            finding["substituted"] = schema["fail_closed"]
            findings.append(finding)
            return schema["fail_closed"], True
        findings.append(finding)
        return value, False

    declared = schema.get("type")
    if declared is not None and not _TYPE_CHECKS[declared](value):
        return _fail(
            TYPE_MISMATCH,
            f"expected {declared}, got {type(value).__name__}",
        )

    allowed = schema.get("enum")
    if allowed is not None and value not in allowed:
        # Case/whitespace normalization is NOT a repair — every enum consumer in
        # this platform already reads its token as ``str(x).strip().lower()``
        # (map_axis, map_grounding_enum, classify_rule_verdict), so "YES " is a
        # conforming "yes" and is canonicalized without a finding. Requiring an
        # exact match here would degrade output to the fail-closed sentinel that
        # the code this module replaced accepted.
        canonical = _normalize_enum_token(value, allowed)
        if canonical is None:
            return _fail(ENUM_VIOLATION, f"{value!r} is not one of {allowed}")
        value = canonical

    if declared == "object":
        return _walk_object(value, schema, path, findings, repair=repair)
    if declared == "array":
        return _walk_array(value, schema, path, findings, repair=repair)
    return value, True


def _walk_object(value: dict, schema: dict, path: str, findings: list[dict], *, repair: bool):
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    out = dict(value)
    ok = True

    for key in required:
        if key in out:
            continue
        sub = props.get(key) or {}
        finding = _finding(MISSING_REQUIRED, f"{path}.{key}", f"required property {key!r} is absent")
        if repair and "fail_closed" in sub:
            finding["repaired"] = True
            finding["substituted"] = sub["fail_closed"]
            out[key] = sub["fail_closed"]
        else:
            ok = False
        findings.append(finding)

    for key, sub in props.items():
        if key not in out:
            continue  # absent and not required — nothing to check
        new_val, sub_ok = _walk(out[key], sub, f"{path}.{key}", findings, repair=repair)
        out[key] = new_val
        ok = ok and sub_ok

    # JSON Schema's default is permissive; only an explicit False rejects extras.
    # Extras are never silently dropped — dropping is a mutation the caller did
    # not ask for, and the whole point here is that repairs are declared.
    if schema.get("additionalProperties") is False:
        for key in out:
            if key not in props:
                findings.append(
                    _finding(
                        ADDITIONAL_PROPERTY,
                        f"{path}.{key}",
                        f"property {key!r} is not declared and additionalProperties is false",
                    )
                )
                ok = False

    return out, ok


def _walk_array(value: list, schema: dict, path: str, findings: list[dict], *, repair: bool):
    ok = True
    min_items = schema.get("minItems")
    max_items = schema.get("maxItems")
    # Length is NOT repaired even when a sentinel is declared: how many items
    # there should be is caller knowledge (content_grounding pads to the number
    # of claims IT segmented), and inventing entries to satisfy a bound would
    # manufacture judgements.
    if isinstance(min_items, int) and len(value) < min_items:
        findings.append(
            _finding(TOO_FEW_ITEMS, path, f"{len(value)} item(s), minimum is {min_items}")
        )
        ok = False
    if isinstance(max_items, int) and len(value) > max_items:
        findings.append(
            _finding(TOO_MANY_ITEMS, path, f"{len(value)} item(s), maximum is {max_items}")
        )
        ok = False

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return value, ok

    out = list(value)
    for i, item in enumerate(out):
        new_val, item_ok = _walk(item, item_schema, f"{path}[{i}]", findings, repair=repair)
        out[i] = new_val
        ok = ok and item_ok
    return out, ok


# ── Parsing ──────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def extract_json_payload(raw_text: str, *, prefer: str = "object") -> Any:
    """Best-effort JSON extraction from a completion. Returns None on failure.

    Handles the three shapes models actually emit: bare JSON, JSON inside a
    ```json fence, and JSON with prose wrapped around it. ``prefer`` picks which
    bracket to hunt for first when the payload is buried in prose.

    Returning None is not an error path to be swallowed — every caller here
    turns it into an ``unparseable`` finding and refuses.
    """
    if not raw_text or not str(raw_text).strip():
        return None
    text = str(raw_text).strip()

    fenced = _FENCE_RE.search(text)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            pass
        patterns = (
            (_ARRAY_RE, _OBJECT_RE) if prefer == "array" else (_OBJECT_RE, _ARRAY_RE)
        )
        for pattern in patterns:
            m = pattern.search(candidate)
            if not m:
                continue
            try:
                return json.loads(m.group(0))
            except (ValueError, TypeError):
                continue
    return None


def coerce_or_reject(raw_text: str, contract: OutputContract, *, mode: str = "reject"):
    """Parse ``raw_text`` and hold it to ``contract``. Returns ``(obj|None, findings)``.

    Args:
        raw_text: the raw completion text.
        contract: the declared shape.
        mode: ``"reject"`` (default) returns ``None`` on ANY finding.
            ``"coerce"`` additionally substitutes each violation's declared
            ``fail_closed`` sentinel, and still returns ``None`` if even one
            violation has no declared sentinel — a partly-valid object is never
            returned in either mode.

    Unparseable or empty input fails CLOSED: ``(None, [finding])``. What to do
    with a refusal (block, or fall back to a deterministic floor) is the
    caller's stated policy, not this module's.
    """
    if mode not in ("reject", "coerce"):
        raise ValueError(f"mode must be 'reject' or 'coerce', got {mode!r}")

    if not raw_text or not str(raw_text).strip():
        return None, [_finding(EMPTY_OUTPUT, "$", "the model returned no text")]

    payload = extract_json_payload(raw_text, prefer=contract.root_type or "object")
    if payload is None:
        return None, [
            _finding(UNPARSEABLE, "$", "no JSON payload could be parsed from the completion")
        ]

    findings: list[dict] = []
    value, ok = _walk(payload, contract.schema, "$", findings, repair=(mode == "coerce"))
    if not ok:
        return None, findings
    if mode == "reject" and findings:
        return None, findings
    return value, findings


def repair_once(
    raw_text: str,
    contract: OutputContract,
    *,
    router,
    function: str,
    max_tokens: int = 800,
):
    """Validate; on failure re-prompt the model EXACTLY once. ``(obj|None, findings)``.

    The retry shows the model its own output and the specific findings, and asks
    for corrected JSON. Bounded to one attempt by construction — not by a loop
    counter a later edit can raise — because a model that cannot satisfy a
    3-field contract on the second try will not on the fifth, and an unbounded
    repair loop is an unbounded spend.

    ``function`` is the ``llm_function`` routing key; this module never names a
    model. A router error is a finding, not an exception: the caller's fallback
    policy still applies.
    """
    obj, findings = coerce_or_reject(raw_text, contract, mode="reject")
    if obj is not None:
        return obj, findings
    for f in findings:
        f["attempt"] = 1

    if router is None:
        return None, findings + [
            _finding(REPAIR_UNAVAILABLE, "$", "no router supplied; repair not attempted")
        ]

    from tools.llm.provider import LLMRequest  # noqa: PLC0415 — keep import cost off the parse path

    defects = "\n".join(f"  - {f['path']}: {f['message']}" for f in findings[:20])
    prompt = (
        "Your previous response did not conform to the required JSON schema. "
        "Return ONLY corrected JSON — no prose, no code fence, no explanation. "
        "Do not invent content: keep every judgement you already made and change "
        "only what the defects require.\n\n"
        f"SCHEMA:\n{contract.prompt_fragment()}\n\n"
        f"DEFECTS:\n{defects}\n\n"
        f"YOUR PREVIOUS RESPONSE:\n{str(raw_text)[:2000]}"
    )
    try:
        resp = router.invoke(
            function,
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.0,
            ),
        )
        retry_text = (getattr(resp, "content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a dead router is a finding, not a crash
        return None, findings + [
            _finding(REPAIR_FAILED, "$", f"repair call failed: {type(exc).__name__}")
        ]

    obj2, findings2 = coerce_or_reject(retry_text, contract, mode="reject")
    for f in findings2:
        f["attempt"] = 2
    return obj2, findings + findings2
