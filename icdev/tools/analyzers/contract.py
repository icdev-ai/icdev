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

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analyzer_contract")

CONTRACT_FILENAME = "analyzer_contract.yaml"

#: Supported contract schema versions. A file declaring anything else is
#: rejected rather than best-effort parsed.
SUPPORTED_VERSIONS: Tuple[int, ...] = (1,)

#: Legal values of ``kind``. Cortex's split: analyzers observe, responders act.
ANALYZER_KINDS: Tuple[str, ...] = ("analyzer", "responder")

_DOTTED_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
#: ``param`` or ``param.key`` — the two forms ``binding.observable_arg`` takes.
_ARG_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")

#: ``package.module:callable`` — the form ``binding.connection.factory`` takes.
_FACTORY_REF = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)

#: How the observable value reaches its parameter. ``list`` wraps the scalar for
#: batch-shaped callables such as ``score_advisories(advisory_ids)`` — without it
#: they receive a bare string where they iterate, and silently score nothing.
_OBSERVABLE_FORMS = frozenset({"scalar", "list"})


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
    """How a callable that takes a DB handle gets one — declared, not coded (anz-mig-01).

    Every ``tools/dsoc_canvas/`` analyzer takes ``conn`` as its first argument,
    and every hand-written call site repeats the same three lines: import the
    canvas ``get_connection``, call it, close it. ``context_args`` can already
    route a caller-supplied connection into that parameter, but that pushes the
    boilerplate onto every caller and leaves the lifecycle unowned — nobody can
    say from the declaration whether the dispatcher or the caller commits.

    Naming the factory here as a ``module:callable`` reference keeps both out of
    a dispatch table. ``commit`` is explicit because these analyzers WRITE: a
    hijack detection that is computed and never committed is a silent no-op of
    exactly the kind the contract exists to remove.

    The factory is resolved at call time, not at load time — a contract that
    imported every canvas's DB module just to validate would drag the whole
    platform into any process that reads the declaration.
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
class ArgumentBinding:
    """How a dispatcher turns an observable plus context into a call (anz-disp-01).

    Declared per analyzer because ICDEV's entrypoints share no signature. The
    observable value is the third argument of ``triage_cve(project_id, cve_id,
    component, ...)``, the whole first argument of ``screen_item(item: dict)``,
    and the *second* of ``trigger_rtbh(conn, prefix, ...)`` — whose first
    argument is a live DB connection. Any "just pass it first" rule hands an IP
    address to ``conn``; the call then fails somewhere inside the analyzer and
    the failure reads exactly like the analyzer having nothing to report.

    So the mapping is declared, in data, next to the analyzer it describes:

    * ``observable_arg`` — parameter that receives the observable value. Use
      ``param.key`` to nest it inside a dict built for ``param`` (that is how
      ``screen_item({"vendor_name": ...})`` is reached). When omitted the value
      goes to the callable's first parameter.
    * ``context_args`` — ``parameter -> dispatch-context key``. A key the caller
      did not supply is reported, not guessed.
    * ``static_args`` — constants passed on every call.

    A parameter left with no source and no default is a *misdeclaration*: the
    dispatcher says so by name rather than calling the analyzer and hoping.
    """

    observable_arg: Optional[str] = None
    observable_form: str = "scalar"
    context_args: Tuple[Tuple[str, str], ...] = ()
    static_args: Tuple[Tuple[str, Any], ...] = ()
    connection: Optional[ConnectionBinding] = None

    @property
    def context_map(self) -> Dict[str, str]:
        """``{parameter: context key}`` as a plain dict."""
        return dict(self.context_args)

    @property
    def static_map(self) -> Dict[str, Any]:
        """``{parameter: constant}`` as a plain dict."""
        return dict(self.static_args)

    @property
    def declared(self) -> bool:
        """True when the contract said anything at all about this analyzer's call."""
        return bool(
            self.observable_arg
            or self.context_args
            or self.static_args
            or self.connection
            or self.observable_form != "scalar"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observable_arg": self.observable_arg,
            "observable_form": self.observable_form,
            "context_args": self.context_map,
            "static_args": self.static_map,
            "connection": self.connection.to_dict() if self.connection else None,
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
    binding: ArgumentBinding = ArgumentBinding()

    def accepts_observable(self, observable_type: str) -> bool:
        """True when this analyzer declared *observable_type* as an input."""
        return observable_type in self.accepts

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
            "binding": self.binding.to_dict(),
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


def _parse_connection(raw: Any, *, what: str) -> Optional[ConnectionBinding]:
    """Validate an optional ``binding.connection`` block (anz-mig-01).

    The factory is checked for SHAPE only. Importing it here to prove it
    resolves would pull every declared canvas's database module into any
    process that merely reads the contract — including the coherence checker
    and the tests — so resolution stays at call time, where the dispatcher can
    report the failure against the analyzer it belongs to.
    """
    if raw is None:
        return None
    block = _require_mapping(raw, f"{what}.connection")

    param = _require_str(block.get("param"), f"{what}.connection.param")
    if not _IDENTIFIER.match(param):
        raise InvalidDeclaration(
            f"{what}.connection.param {param!r} must be a Python parameter name"
        )

    factory = _require_str(block.get("factory"), f"{what}.connection.factory")
    if not _FACTORY_REF.match(factory):
        raise InvalidDeclaration(
            f"{what}.connection.factory must be `module.path:callable`, got {factory!r}"
        )

    commit = block.get("commit", False)
    if not isinstance(commit, bool):
        raise InvalidDeclaration(
            f"{what}.connection.commit must be true or false, got {commit!r}"
        )

    unknown = sorted(set(block) - {"param", "factory", "commit"})
    if unknown:
        raise InvalidDeclaration(f"{what}.connection has unknown key(s) {unknown}")

    return ConnectionBinding(param=param, factory=factory, commit=commit)


def _parse_binding(raw: Any, *, analyzer_key: str) -> ArgumentBinding:
    """Validate an optional ``binding:`` block. Absent means "first parameter"."""
    if raw is None:
        return ArgumentBinding()
    what = f"analyzer {analyzer_key!r} `binding`"
    block = _require_mapping(raw, what)

    observable_arg = block.get("observable_arg")
    if observable_arg is not None:
        observable_arg = _require_str(observable_arg, f"{what}.observable_arg")
        if not _ARG_TARGET.match(observable_arg):
            raise InvalidDeclaration(
                f"{what}.observable_arg must be `parameter` or `parameter.key`, "
                f"got {observable_arg!r}"
            )

    def _pairs(section: str, *, values_must_be_str: bool) -> Tuple[Tuple[str, Any], ...]:
        value = block.get(section)
        if value is None:
            return ()
        mapping = _require_mapping(value, f"{what}.{section}")
        out: List[Tuple[str, Any]] = []
        for param, source in mapping.items():
            param = _require_str(param, f"{what}.{section} key")
            if not _IDENTIFIER.match(param):
                raise InvalidDeclaration(
                    f"{what}.{section} key {param!r} must be a Python parameter name"
                )
            if values_must_be_str:
                source = _require_str(source, f"{what}.{section}.{param}")
            out.append((param, source))
        return tuple(out)

    observable_form = block.get("observable_form", "scalar")
    observable_form = _require_str(observable_form, f"{what}.observable_form")
    if observable_form not in _OBSERVABLE_FORMS:
        raise InvalidDeclaration(
            f"{what}.observable_form must be one of "
            f"{sorted(_OBSERVABLE_FORMS)}, got {observable_form!r}"
        )

    connection = _parse_connection(block.get("connection"), what=what)

    context_args = _pairs("context_args", values_must_be_str=True)
    static_args = _pairs("static_args", values_must_be_str=False)

    # Two sources for one parameter is a declaration defect, not a precedence
    # question — refuse to guess which one the author meant.
    context_params = {p for p, _ in context_args}
    static_params = {p for p, _ in static_args}
    clash = sorted(context_params & static_params)
    if clash:
        raise InvalidDeclaration(
            f"{what}: parameter(s) {clash} are supplied by both `context_args` "
            "and `static_args`"
        )
    if observable_arg:
        head = observable_arg.split(".", 1)[0]
        if head in context_params or head in static_params:
            raise InvalidDeclaration(
                f"{what}: parameter {head!r} receives the observable and is also "
                "supplied by `context_args`/`static_args`"
            )

    # The connection parameter is a fourth source, and the same rule applies to
    # it: one parameter, one source. Silently letting `context_args` win would
    # hand the analyzer whatever the caller passed and never open the factory,
    # which reads at runtime as the analyzer simply not writing anything.
    if connection is not None:
        if connection.param in context_params or connection.param in static_params:
            raise InvalidDeclaration(
                f"{what}: parameter {connection.param!r} is supplied by "
                "`connection` and also by `context_args`/`static_args`"
            )
        if observable_arg and observable_arg.split(".", 1)[0] == connection.param:
            raise InvalidDeclaration(
                f"{what}: parameter {connection.param!r} receives both the "
                "observable and the connection"
            )

    return ArgumentBinding(
        observable_arg=observable_arg,
        observable_form=observable_form,
        context_args=context_args,
        static_args=static_args,
        connection=connection,
    )


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
        binding=_parse_binding(block.get("binding"), analyzer_key=key),
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
            print(
                f"{decl.key:24} {decl.kind:10} {state:9} "
                f"accepts={','.join(decl.accepts)} taxonomy={decl.taxonomy.namespace} "
                f"sandbox={decl.sandbox}"
            )
        return 0

    print(
        f"VALID: {contract.path} — {len(contract.observable_types)} observable types, "
        f"{len(contract.analyzers)} declaration(s), "
        f"{len(contract.taxonomy_namespaces)} taxonomy namespace(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
