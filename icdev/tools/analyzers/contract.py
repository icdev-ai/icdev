#!/usr/bin/env python3
# CUI // SP-CTI
"""Loader and validator for the ICDEV™ analyzer/responder contract (anz-con-01).

The contract itself is DATA — ``args/analyzer_contract.yaml``. A new analyzer is
declared entirely there: the observable types it accepts, the taxonomy it emits,
its rate limit and its sandbox posture. No base class, no dispatch table, no
blueprint edit. That is deliberate: ICDEV has ~79 analyzer-shaped modules across
``tools/strategos/``, ``tools/security/``, ``tools/supply_chain/`` and
``tools/dsoc_canvas/`` with no shared base class between them, and a contract
that requires rewriting a module to adopt does not get adopted.

This module's one job is to make a bad declaration fail LOUDLY AT LOAD:

    >>> from tools.analyzers.contract import load_contract
    >>> contract = load_contract()          # raises on any invalid declaration
    >>> contract.for_observable("cve")[0].key
    'cve_triage'

Why that matters here specifically: this repo has already shipped an
open-looking vocabulary that was actually closed. ``register_citation`` raised
``ValueError`` on an unknown ``citation_type``, every caller swallowed it, and
two subsystems wrote zero provenance rows for their entire existence while the
gate reported ``warn`` (see ``tools/provenance/citation_types.py``). So the
observable vocabulary here is closed, validation runs when the file is parsed
rather than when an observable is dispatched, and the errors below are not
caught anywhere in this module.

The same constant renders the SQL CHECK clause for any column that stores an
observable type (:func:`check_constraint_sql`, :func:`sqlite_check_clause`), so
a future ``analyzer_reports`` table and this file cannot disagree — the repo
guardrail "SQL CHECK constraints: derive from Python constants, never hardcode".

Usage:
    python tools/analyzers/contract.py --validate
    python tools/analyzers/contract.py --json
    python tools/analyzers/contract.py --list
    python tools/analyzers/contract.py --observable cve
    python tools/analyzers/contract.py --check-sql observable_type
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_contract")

CONTRACT_FILENAME = "analyzer_contract.yaml"

#: Supported contract schema versions. A file declaring anything else is
#: rejected rather than best-effort parsed.
SUPPORTED_VERSIONS: Tuple[int, ...] = (1,)

#: Legal values of ``kind``. Cortex's split: analyzers observe, responders act.
ANALYZER_KINDS: Tuple[str, ...] = ("analyzer", "responder")

#: How the observable value reaches the callable. ``scalar`` passes it as-is;
#: ``list`` wraps it in a one-element list for batch-shaped callables such as
#: ``vuln_triage_engine.score_advisories(advisory_ids=[...])``.
OBSERVABLE_FORMS: Tuple[str, ...] = ("scalar", "list")

_DOTTED_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_FACTORY_REF = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)


# ---------------------------------------------------------------------------
# Errors — none of these are caught in this module. That is the point.
# ---------------------------------------------------------------------------


class ContractError(ValueError):
    """Base class for every analyzer-contract declaration defect."""


class ContractFileError(ContractError):
    """The contract file is missing, unreadable, or not a mapping."""


class UnknownObservableType(ContractError):
    """An analyzer declared an observable type outside the closed vocabulary."""


class UnknownTaxonomyValue(ContractError):
    """An analyzer declared a taxonomy namespace or level outside the closed set."""


class UnknownSandboxPosture(ContractError):
    """An analyzer declared a sandbox posture outside the closed set."""


class DuplicateAnalyzerKey(ContractError):
    """Two analyzers were declared with the same key."""


class InvalidDeclaration(ContractError):
    """A declaration is structurally wrong: missing field, bad type, bad shape."""


class InvalidInputBinding(ContractError):
    """An ``input_binding`` block is structurally wrong or self-contradictory."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ObservableType:
    """One entry of the closed observable vocabulary."""

    key: str
    description: str
    consumers: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "description": self.description,
            "consumers": list(self.consumers),
        }


@dataclasses.dataclass(frozen=True)
class RateLimit:
    """Per-analyzer call ceiling. Enforcement lands in anz-rate-01."""

    max_calls: int
    per_seconds: int

    def to_dict(self) -> Dict[str, Any]:
        return {"max_calls": self.max_calls, "per_seconds": self.per_seconds}


@dataclasses.dataclass(frozen=True)
class TaxonomyDeclaration:
    """What an analyzer emits: a closed namespace, its own predicates, closed levels."""

    namespace: str
    predicates: Tuple[str, ...]
    levels: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "predicates": list(self.predicates),
            "levels": list(self.levels),
        }


@dataclasses.dataclass(frozen=True)
class ConnectionBinding:
    """How a callable that takes a DB handle gets one — declared, not coded.

    Every ``tools/dsoc_canvas/`` analyzer takes ``conn`` as its first argument
    and every hand-written call site repeats the same three lines: import the
    canvas ``get_connection``, call, close. Naming the factory here as a
    ``module:callable`` reference keeps that out of a dispatch table.
    """

    param: str
    factory: str
    commit: bool = False

    @property
    def factory_module(self) -> str:
        return self.factory.split(":", 1)[0]

    @property
    def factory_attr(self) -> str:
        return self.factory.split(":", 1)[1]

    def to_dict(self) -> Dict[str, Any]:
        return {"param": self.param, "factory": self.factory, "commit": self.commit}


@dataclasses.dataclass(frozen=True)
class InputBinding:
    """How an observable reaches an already-existing callable's parameters.

    anz-con-01 deliberately stopped at declaring WHAT an analyzer accepts,
    leaving argument adaptation open. The seed set showed why that has to be
    data too: the six declared entrypoints have six incompatible signatures —
    ``match_observable(observable, context='')`` takes the observable first,
    ``trigger_rtbh(conn, prefix, reason, ...)`` wants a DB handle first, and
    ``triage_cve(project_id, cve_id, component, cvss_score, severity,
    description, ...)`` buries it in argument two of six. Writing one adapter
    function per analyzer would rebuild exactly the bespoke wiring this card
    exists to delete, so the mapping is declared here instead.

    Nothing about the target callable changes. ``tools/analyzers/binding.py``
    reads this block, assembles keyword arguments, and returns the callable's
    result untouched — no wrapping, no coercion. That output transparency is
    what makes "port an analyzer" a null behavioral change.
    """

    observable_param: str
    observable_form: str = "scalar"
    connection: Optional[ConnectionBinding] = None
    context_params: Tuple[str, ...] = ()
    optional_context_params: Tuple[str, ...] = ()

    def bound_param_names(self) -> Tuple[str, ...]:
        """Every callable parameter this binding supplies a value for."""
        names = [self.observable_param]
        if self.connection is not None:
            names.append(self.connection.param)
        names.extend(self.context_params)
        names.extend(self.optional_context_params)
        return tuple(names)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observable_param": self.observable_param,
            "observable_form": self.observable_form,
            "connection": self.connection.to_dict() if self.connection else None,
            "context_params": list(self.context_params),
            "optional_context_params": list(self.optional_context_params),
        }


@dataclasses.dataclass(frozen=True)
class AnalyzerDeclaration:
    """One declared analyzer or responder. Declared in config, not in code."""

    key: str
    kind: str
    display_name: str
    description: str
    module: str
    entrypoint: str
    accepts: Tuple[str, ...]
    taxonomy: TaxonomyDeclaration
    rate_limit: RateLimit
    sandbox: str
    timeout_seconds: int
    enabled: bool
    input_binding: Optional[InputBinding] = None

    def accepts_observable(self, observable_type: str) -> bool:
        """True when this analyzer declared *observable_type* as an input."""
        return observable_type in self.accepts

    @property
    def is_dispatchable(self) -> bool:
        """True when this declaration can be invoked, not merely described.

        A declaration without an ``input_binding`` is a statement of intent:
        it says what the analyzer accepts and emits, but nothing knows how to
        hand it an observable. :func:`tools.analyzers.binding.verify_bindings`
        lists these so "declared" is never mistaken for "wired".
        """
        return self.input_binding is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "display_name": self.display_name,
            "description": self.description,
            "module": self.module,
            "entrypoint": self.entrypoint,
            "accepts": list(self.accepts),
            "taxonomy": self.taxonomy.to_dict(),
            "rate_limit": self.rate_limit.to_dict(),
            "sandbox": self.sandbox,
            "timeout_seconds": self.timeout_seconds,
            "enabled": self.enabled,
            "input_binding": (
                self.input_binding.to_dict() if self.input_binding else None
            ),
        }


@dataclasses.dataclass(frozen=True)
class AnalyzerContract:
    """The whole contract: closed vocabularies plus every declaration."""

    version: int
    path: Path
    observable_types: Mapping[str, ObservableType]
    taxonomy_levels: Mapping[str, str]
    taxonomy_namespaces: Mapping[str, str]
    sandbox_postures: Mapping[str, str]
    analyzers: Tuple[AnalyzerDeclaration, ...]

    # -- lookups ----------------------------------------------------------

    def get(self, key: str) -> Optional[AnalyzerDeclaration]:
        """The declaration named *key*, or ``None``."""
        for decl in self.analyzers:
            if decl.key == key:
                return decl
        return None

    def for_observable(
        self, observable_type: str, *, kind: Optional[str] = None, enabled_only: bool = True
    ) -> List[AnalyzerDeclaration]:
        """Every declaration that accepts *observable_type*.

        Raises :class:`UnknownObservableType` for a type outside the closed
        vocabulary — a typo at a call site must not silently return no
        analyzers and read as "nothing applies".
        """
        if observable_type not in self.observable_types:
            raise UnknownObservableType(
                f"unknown observable type {observable_type!r}; "
                f"legal values: {', '.join(sorted(self.observable_types))}"
            )
        out = [d for d in self.analyzers if d.accepts_observable(observable_type)]
        if kind is not None:
            out = [d for d in out if d.kind == kind]
        if enabled_only:
            out = [d for d in out if d.enabled]
        return out

    def is_valid_observable_type(self, observable_type: str) -> bool:
        """True when *observable_type* is in the closed vocabulary."""
        return observable_type in self.observable_types

    def dispatchable(self) -> List[AnalyzerDeclaration]:
        """Every declaration that carries an ``input_binding``, in file order."""
        return [d for d in self.analyzers if d.is_dispatchable]

    # -- machine-readable export -----------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """The whole contract as plain JSON-serializable data."""
        return {
            "version": self.version,
            "source": str(self.path),
            "observable_types": {k: v.to_dict() for k, v in self.observable_types.items()},
            "taxonomy": {
                "levels": dict(self.taxonomy_levels),
                "namespaces": dict(self.taxonomy_namespaces),
            },
            "sandbox_postures": dict(self.sandbox_postures),
            "analyzers": [d.to_dict() for d in self.analyzers],
        }

    # -- SQL guard --------------------------------------------------------

    def check_constraint_sql(self, column: str = "observable_type") -> str:
        """PostgreSQL CHECK clause for *column*, rendered from the vocabulary."""
        values = ", ".join(f"'{t}'" for t in sorted(self.observable_types))
        return f"CHECK ({column} IN ({values}))"

    def sqlite_check_clause(self, column: str = "observable_type") -> str:
        """CHECK clause in the inline form SQLite CREATE TABLE statements use."""
        values = ",".join(f"'{t}'" for t in sorted(self.observable_types))
        return f"CHECK({column} IN ({values}))"


# ---------------------------------------------------------------------------
# File resolution
# ---------------------------------------------------------------------------


def find_contract_path(start: Optional[Path] = None) -> Path:
    """Locate ``args/analyzer_contract.yaml``.

    Two ordered passes so a source checkout and an installed wheel resolve to
    exactly ONE file. Pass 1 looks for ``<parent>/args/`` at every level, pass 2
    for ``<parent>/data/args/`` (the wheel layout, ``icdev/data/args/``). Doing
    it in that order — rather than probing both layouts per level — means that
    when this module is imported as ``icdev.tools.analyzers.contract`` in a
    checkout it still resolves to the repo-root ``args/`` copy, not to a
    packaged duplicate. Two copies read by two import namespaces is how
    ``llm_config.yaml`` ended up with three drifting copies.
    """
    here = (start or Path(__file__)).resolve()
    for rel in (("args",), ("data", "args")):
        for parent in here.parents:
            candidate = parent.joinpath(*rel, CONTRACT_FILENAME)
            if candidate.is_file():
                return candidate
    raise ContractFileError(
        f"{CONTRACT_FILENAME} not found in any args/ or data/args/ directory "
        f"above {here}"
    )


# ---------------------------------------------------------------------------
# Validation helpers — every one of these raises; none of them warn.
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidDeclaration(f"{what} must be a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidDeclaration(f"{what} must be a non-empty string, got {value!r}")
    return value


def _require_str_list(value: Any, what: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidDeclaration(f"{what} must be a non-empty list, got {value!r}")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidDeclaration(f"{what} entries must be non-empty strings, got {item!r}")
    return tuple(value)


def _require_positive_int(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidDeclaration(f"{what} must be a positive integer, got {value!r}")
    return value


def _parse_vocabulary(raw: Any, section: str) -> Dict[str, str]:
    """A ``{key: description}`` closed vocabulary block."""
    block = _require_mapping(raw, f"`{section}`")
    if not block:
        raise InvalidDeclaration(f"`{section}` must declare at least one value")
    out: Dict[str, str] = {}
    for key, description in block.items():
        key = _require_str(key, f"`{section}` key")
        out[key] = _require_str(description, f"`{section}.{key}`")
    return out


def _parse_observable_types(raw: Any) -> Dict[str, ObservableType]:
    block = _require_mapping(raw, "`observable_types`")
    if not block:
        raise InvalidDeclaration("`observable_types` must declare at least one type")
    out: Dict[str, ObservableType] = {}
    for key, body in block.items():
        key = _require_str(key, "`observable_types` key")
        if not _KEY.match(key):
            raise InvalidDeclaration(
                f"observable type {key!r} must be lower_snake_case "
                "so it is safe as a SQL literal and a JSON key"
            )
        body = _require_mapping(body, f"`observable_types.{key}`")
        consumers = body.get("consumers", [])
        if consumers and not isinstance(consumers, list):
            raise InvalidDeclaration(f"`observable_types.{key}.consumers` must be a list")
        out[key] = ObservableType(
            key=key,
            description=_require_str(
                body.get("description"), f"`observable_types.{key}.description`"
            ),
            consumers=tuple(str(c) for c in (consumers or [])),
        )
    return out


def _parse_rate_limit(raw: Any, what: str) -> RateLimit:
    block = _require_mapping(raw, what)
    return RateLimit(
        max_calls=_require_positive_int(block.get("max_calls"), f"{what}.max_calls"),
        per_seconds=_require_positive_int(block.get("per_seconds"), f"{what}.per_seconds"),
    )


def _parse_taxonomy(
    raw: Any,
    *,
    analyzer_key: str,
    namespaces: Iterable[str],
    levels: Iterable[str],
) -> TaxonomyDeclaration:
    what = f"analyzer {analyzer_key!r} `taxonomy`"
    block = _require_mapping(raw, what)

    namespace = _require_str(block.get("namespace"), f"{what}.namespace")
    legal_namespaces = set(namespaces)
    if namespace not in legal_namespaces:
        raise UnknownTaxonomyValue(
            f"analyzer {analyzer_key!r} declares taxonomy namespace {namespace!r}, "
            f"which is not in the closed set: {', '.join(sorted(legal_namespaces))}"
        )

    predicates = _require_str_list(block.get("predicates"), f"{what}.predicates")

    legal_levels = set(levels)
    declared_levels = block.get("levels")
    if declared_levels is None:
        resolved_levels: Tuple[str, ...] = tuple(levels)
    else:
        resolved_levels = _require_str_list(declared_levels, f"{what}.levels")
        unknown = [lvl for lvl in resolved_levels if lvl not in legal_levels]
        if unknown:
            raise UnknownTaxonomyValue(
                f"analyzer {analyzer_key!r} declares taxonomy level(s) {unknown}, "
                f"which are not in the closed set: {', '.join(sorted(legal_levels))}"
            )
    return TaxonomyDeclaration(
        namespace=namespace, predicates=predicates, levels=resolved_levels
    )


def _require_identifier_list(value: Any, what: str) -> Tuple[str, ...]:
    """A possibly-empty list of Python identifiers, no duplicates."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise InvalidInputBinding(f"{what} must be a list, got {value!r}")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not _IDENTIFIER.match(item):
            raise InvalidInputBinding(
                f"{what} entries must be Python identifiers, got {item!r}"
            )
        if item in out:
            raise InvalidInputBinding(f"{what} lists {item!r} more than once")
        out.append(item)
    return tuple(out)


def _parse_connection_binding(raw: Any, what: str) -> ConnectionBinding:
    block = _require_mapping(raw, what)
    param = block.get("param")
    if not isinstance(param, str) or not _IDENTIFIER.match(param):
        raise InvalidInputBinding(
            f"{what}.param must be a Python identifier, got {param!r}"
        )
    factory = block.get("factory")
    if not isinstance(factory, str) or not _FACTORY_REF.match(factory):
        raise InvalidInputBinding(
            f"{what}.factory must be a 'dotted.module:callable' reference, got {factory!r}"
        )
    commit = block.get("commit", False)
    if not isinstance(commit, bool):
        raise InvalidInputBinding(f"{what}.commit must be a boolean, got {commit!r}")
    return ConnectionBinding(param=param, factory=factory, commit=commit)


def _parse_input_binding(raw: Any, *, analyzer_key: str) -> InputBinding:
    what = f"analyzer {analyzer_key!r} `input_binding`"
    block = _require_mapping(raw, what)

    observable_param = block.get("observable_param")
    if not isinstance(observable_param, str) or not _IDENTIFIER.match(observable_param):
        raise InvalidInputBinding(
            f"{what}.observable_param must be a Python identifier, got {observable_param!r}"
        )

    observable_form = block.get("observable_form", "scalar")
    if observable_form not in OBSERVABLE_FORMS:
        raise InvalidInputBinding(
            f"{what}.observable_form is {observable_form!r}; "
            f"legal values: {', '.join(OBSERVABLE_FORMS)}"
        )

    connection = (
        _parse_connection_binding(block["connection"], f"{what}.connection")
        if block.get("connection") is not None
        else None
    )

    context_params = _require_identifier_list(
        block.get("context_params"), f"{what}.context_params"
    )
    optional_context_params = _require_identifier_list(
        block.get("optional_context_params"), f"{what}.optional_context_params"
    )

    binding = InputBinding(
        observable_param=observable_param,
        observable_form=observable_form,
        connection=connection,
        context_params=context_params,
        optional_context_params=optional_context_params,
    )

    # One parameter, one source. A name claimed twice means the binding is
    # ambiguous about which value wins, and picking a winner silently is how a
    # migration stops being behaviour-preserving.
    names = binding.bound_param_names()
    collisions = sorted({n for n in names if names.count(n) > 1})
    if collisions:
        raise InvalidInputBinding(
            f"{what} binds parameter(s) {collisions} from more than one source; "
            "each callable parameter may be supplied by exactly one of "
            "observable_param, connection.param, context_params, optional_context_params"
        )
    return binding


def _parse_analyzer(
    raw: Any,
    index: int,
    *,
    observable_types: Mapping[str, ObservableType],
    namespaces: Mapping[str, str],
    levels: Mapping[str, str],
    sandbox_postures: Mapping[str, str],
    defaults: Mapping[str, Any],
) -> AnalyzerDeclaration:
    block = _require_mapping(raw, f"`analyzers[{index}]`")
    key = _require_str(block.get("key"), f"`analyzers[{index}].key`")
    if not _KEY.match(key):
        raise InvalidDeclaration(f"analyzer key {key!r} must be lower_snake_case")

    kind = block.get("kind", "analyzer")
    if kind not in ANALYZER_KINDS:
        raise InvalidDeclaration(
            f"analyzer {key!r} declares kind {kind!r}; legal values: {', '.join(ANALYZER_KINDS)}"
        )

    module = _require_str(block.get("module"), f"analyzer {key!r} `module`")
    if not _DOTTED_PATH.match(module):
        raise InvalidDeclaration(
            f"analyzer {key!r} `module` must be a dotted import path, got {module!r}"
        )
    entrypoint = _require_str(block.get("entrypoint"), f"analyzer {key!r} `entrypoint`")
    if not _IDENTIFIER.match(entrypoint):
        raise InvalidDeclaration(
            f"analyzer {key!r} `entrypoint` must be a Python identifier, got {entrypoint!r}"
        )

    accepts = _require_str_list(block.get("accepts"), f"analyzer {key!r} `accepts`")
    unknown = [obs for obs in accepts if obs not in observable_types]
    if unknown:
        # The whole point of anz-con-01: loud, at load, naming the offender and
        # the legal values. NOT a warn, NOT at dispatch.
        raise UnknownObservableType(
            f"analyzer {key!r} accepts unknown observable type(s) {unknown}. "
            f"Legal values: {', '.join(sorted(observable_types))}. "
            f"Add the type to `observable_types` in {CONTRACT_FILENAME} "
            "(with the module that consumes it) before declaring it here."
        )
    duplicates = sorted({obs for obs in accepts if accepts.count(obs) > 1})
    if duplicates:
        raise InvalidDeclaration(
            f"analyzer {key!r} lists observable type(s) {duplicates} more than once"
        )

    sandbox = block.get("sandbox", defaults["sandbox"])
    if sandbox not in sandbox_postures:
        raise UnknownSandboxPosture(
            f"analyzer {key!r} declares sandbox posture {sandbox!r}; "
            f"legal values: {', '.join(sorted(sandbox_postures))}"
        )

    rate_limit = (
        _parse_rate_limit(block["rate_limit"], f"analyzer {key!r} `rate_limit`")
        if "rate_limit" in block
        else defaults["rate_limit"]
    )

    timeout_seconds = (
        _require_positive_int(block["timeout_seconds"], f"analyzer {key!r} `timeout_seconds`")
        if "timeout_seconds" in block
        else defaults["timeout_seconds"]
    )

    enabled = block.get("enabled", defaults["enabled"])
    if not isinstance(enabled, bool):
        raise InvalidDeclaration(f"analyzer {key!r} `enabled` must be a boolean, got {enabled!r}")

    return AnalyzerDeclaration(
        key=key,
        kind=kind,
        display_name=_require_str(
            block.get("display_name", key), f"analyzer {key!r} `display_name`"
        ),
        description=str(block.get("description", "")),
        module=module,
        entrypoint=entrypoint,
        accepts=accepts,
        taxonomy=_parse_taxonomy(
            block.get("taxonomy"),
            analyzer_key=key,
            namespaces=namespaces,
            levels=levels,
        ),
        rate_limit=rate_limit,
        sandbox=sandbox,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
        input_binding=(
            _parse_input_binding(block["input_binding"], analyzer_key=key)
            if block.get("input_binding") is not None
            else None
        ),
    )


def _parse_defaults(
    raw: Any, *, sandbox_postures: Mapping[str, str]
) -> Dict[str, Any]:
    block = _require_mapping(raw, "`defaults`")
    sandbox = _require_str(block.get("sandbox"), "`defaults.sandbox`")
    if sandbox not in sandbox_postures:
        raise UnknownSandboxPosture(
            f"`defaults.sandbox` is {sandbox!r}; "
            f"legal values: {', '.join(sorted(sandbox_postures))}"
        )
    enabled = block.get("enabled", True)
    if not isinstance(enabled, bool):
        raise InvalidDeclaration(f"`defaults.enabled` must be a boolean, got {enabled!r}")
    return {
        "rate_limit": _parse_rate_limit(block.get("rate_limit"), "`defaults.rate_limit`"),
        "sandbox": sandbox,
        "timeout_seconds": _require_positive_int(
            block.get("timeout_seconds"), "`defaults.timeout_seconds`"
        ),
        "enabled": enabled,
    }


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def parse_contract(data: Any, path: Path) -> AnalyzerContract:
    """Validate an already-parsed contract mapping. Raises on any defect."""
    root = _require_mapping(data, f"{path}")

    version = root.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise InvalidDeclaration(
            f"{path}: unsupported contract version {version!r}; "
            f"supported: {', '.join(str(v) for v in SUPPORTED_VERSIONS)}"
        )

    observable_types = _parse_observable_types(root.get("observable_types"))

    taxonomy = _require_mapping(root.get("taxonomy"), "`taxonomy`")
    levels = _parse_vocabulary(taxonomy.get("levels"), "taxonomy.levels")
    namespaces = _parse_vocabulary(taxonomy.get("namespaces"), "taxonomy.namespaces")
    sandbox_postures = _parse_vocabulary(root.get("sandbox_postures"), "sandbox_postures")
    defaults = _parse_defaults(root.get("defaults"), sandbox_postures=sandbox_postures)

    raw_analyzers = root.get("analyzers")
    if not isinstance(raw_analyzers, list):
        raise InvalidDeclaration("`analyzers` must be a list")

    declarations: List[AnalyzerDeclaration] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(raw_analyzers):
        decl = _parse_analyzer(
            raw,
            index,
            observable_types=observable_types,
            namespaces=namespaces,
            levels=levels,
            sandbox_postures=sandbox_postures,
            defaults=defaults,
        )
        if decl.key in seen:
            raise DuplicateAnalyzerKey(
                f"analyzer key {decl.key!r} declared twice "
                f"(entries {seen[decl.key]} and {index})"
            )
        seen[decl.key] = index
        declarations.append(decl)

    return AnalyzerContract(
        version=version,
        path=path,
        observable_types=observable_types,
        taxonomy_levels=levels,
        taxonomy_namespaces=namespaces,
        sandbox_postures=sandbox_postures,
        analyzers=tuple(declarations),
    )


def load_contract(path: Optional[Path] = None) -> AnalyzerContract:
    """Read and validate the contract. Every defect raises :class:`ContractError`.

    Nothing in this module catches these. A caller that wraps this in a bare
    ``except Exception`` and continues has reintroduced the citation_type bug.
    """
    contract_path = Path(path) if path else find_contract_path()
    try:
        raw_text = contract_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractFileError(f"cannot read {contract_path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ContractFileError(f"{contract_path} is not valid YAML: {exc}") from exc

    contract = parse_contract(data, contract_path)
    logger.debug(
        "analyzer contract loaded: %d observable types, %d declarations from %s",
        len(contract.observable_types),
        len(contract.analyzers),
        contract_path,
    )
    return contract


_CACHED: Optional[AnalyzerContract] = None


def get_contract(*, refresh: bool = False) -> AnalyzerContract:
    """Process-cached :func:`load_contract`. Pass ``refresh=True`` to re-read."""
    global _CACHED
    if _CACHED is None or refresh:
        _CACHED = load_contract()
    return _CACHED


# ---------------------------------------------------------------------------
# Module-level conveniences (mirrors tools/provenance/citation_types.py)
# ---------------------------------------------------------------------------


def observable_types() -> Tuple[str, ...]:
    """Every legal observable type, sorted."""
    return tuple(sorted(get_contract().observable_types))


def is_valid_observable_type(observable_type: str) -> bool:
    """True when *observable_type* is in the closed vocabulary."""
    return get_contract().is_valid_observable_type(observable_type)


def check_constraint_sql(column: str = "observable_type") -> str:
    """PostgreSQL CHECK clause rendered from the closed vocabulary.

    Single source of truth for the SQL: a migration storing observable types
    calls this instead of retyping the list.
    """
    return get_contract().check_constraint_sql(column)


def sqlite_check_clause(column: str = "observable_type") -> str:
    """CHECK clause in the inline form SQLite CREATE TABLE statements use."""
    return get_contract().sqlite_check_clause(column)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load and validate the ICDEV analyzer/responder contract."
    )
    parser.add_argument("--path", help="Contract file (default: args/analyzer_contract.yaml)")
    parser.add_argument("--validate", action="store_true", help="Validate and exit")
    parser.add_argument("--json", action="store_true", help="Emit the contract as JSON")
    parser.add_argument("--list", action="store_true", help="List declared analyzers")
    parser.add_argument("--observable", help="List analyzers accepting this observable type")
    parser.add_argument(
        "--check-sql",
        nargs="?",
        const="observable_type",
        help="Print the PostgreSQL CHECK clause for a column (default: observable_type)",
    )
    args = parser.parse_args(argv)

    try:
        contract = load_contract(Path(args.path) if args.path else None)
    except ContractError as exc:
        payload = {"valid": False, "error": type(exc).__name__, "message": str(exc)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"INVALID: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(contract.to_dict(), indent=2))
        return 0

    if args.check_sql:
        print(contract.check_constraint_sql(args.check_sql))
        return 0

    if args.observable:
        try:
            matches = contract.for_observable(args.observable)
        except UnknownObservableType as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        for decl in matches:
            print(f"{decl.key:24} {decl.kind:10} {decl.module}:{decl.entrypoint}")
        return 0

    if args.list:
        for decl in contract.analyzers:
            state = "enabled" if decl.enabled else "disabled"
            wiring = "bound" if decl.is_dispatchable else "declared"
            print(
                f"{decl.key:24} {decl.kind:10} {state:9} {wiring:9} "
                f"accepts={','.join(decl.accepts)} taxonomy={decl.taxonomy.namespace} "
                f"sandbox={decl.sandbox}"
            )
        return 0

    print(
        f"VALID: {contract.path} — {len(contract.observable_types)} observable types, "
        f"{len(contract.analyzers)} declaration(s) "
        f"({len(contract.dispatchable())} with an input_binding), "
        f"{len(contract.taxonomy_namespaces)} taxonomy namespace(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
