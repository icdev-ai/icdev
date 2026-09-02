# CUI // SP-CTI
"""ICDEV™ Asset Discovery — the ONE adapter contract (rmf-disc-01).

WHY THIS EXISTS

``tools/network/discovery.py`` (946 lines, SNMP/SSH/ping) and
``tools/network/enclave_scanner.py`` (1,093 lines) have ZERO importers, so
neither carries any evidence of working. ``ni_devices`` — the table the
de-facto standard learner reads as ``inventory`` evidence
(``args/docmod/inventory_feeds.yaml``) — is written by exactly one path today,
``network_ingester._persist_to_devices``, from a hand-drawn diagram. The NMS
pull route (``POST /api/network/ingest/nms``) returns devices as JSON to the
caller and persists nothing.

This module is the seam that lets ni_devices be populated by WHICHEVER sources
a deployment actually has, without any one of them being load-bearing.

THE CONTRACT IS DELIBERATELY SMALL — two methods.

``health()`` answers "can this adapter speak to its source RIGHT NOW", and
``discover()`` answers "what devices does the source report". Nothing else. The
existing ``NMSAdapter`` ABC (tools/network/nms_adapter.py) is a five-method
NMS-pull contract and stays exactly as it is; the NetBox adapter here DELEGATES
to it rather than reimplementing a NetBox client. A CSV file and a GNS3 lab
cannot supply ``pull_stats``, and widening this contract to the union of what
five sources can do would make every adapter mostly stubs.

HEALTH IS SEVEN STATES AND THEY ARE NEVER MERGED

The whole point of health here is to answer "is the estate unmeasured, or
measured and empty" — so the states that mean "we did not look" must survive
all the way to the report. ``disabled`` says nothing whatever about the source.
``unavailable`` (a python dependency is absent) and ``unreachable`` (the
network did not answer) have different repairs and must not read as one number.
``unmeasured`` is the value a health field holds before anybody asked, and it
is NEVER folded into a healthy or an unhealthy count.

ONLY A DISCOVERING STATE DISCOVERS. ``run`` skips an adapter whose health is
not in :data:`DISCOVERING_STATES`, and it reports the skip with its state, so
"0 devices" always carries the reason.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.assets.discovery_adapters")


# ── Health vocabulary ─────────────────────────────────────────────────────────

#: Every value ``AdapterHealth.state`` may take. Ordered worst-known-first is
#: deliberately NOT attempted — these are not a severity ladder, they are seven
#: different facts, three of which ("we did not look") have no severity at all.
HEALTH_STATES: tuple[str, ...] = (
    "healthy",       # source answered, in full
    "degraded",      # source answered, but not for every target asked
    "unreachable",   # configured, asked, no answer — a network/credential fix
    "unavailable",   # a declared python dependency is ABSENT — an install fix
    "unconfigured",  # enabled, but required configuration is missing
    "disabled",      # turned off in config. Says NOTHING about the source.
    "unmeasured",    # nobody asked yet. NEVER a clean bill of health.
)

#: The only states from which :func:`~tools.assets.discovery_adapters.runner.run`
#: will call ``discover()``. A ``degraded`` adapter is included on purpose: a
#: partial inventory is evidence, and it carries its own ``detail``.
DISCOVERING_STATES: tuple[str, ...] = ("healthy", "degraded")

#: States meaning "the source was never asked". Counting one of these as a
#: negative verdict about the estate is the defect this module exists to refuse.
NOT_MEASURED_STATES: tuple[str, ...] = ("disabled", "unconfigured", "unmeasured")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Canonical records ─────────────────────────────────────────────────────────


@dataclass
class AdapterHealth:
    """One adapter instance's answer to "can you speak to your source".

    ``state`` is one of :data:`HEALTH_STATES`. ``detail`` is free text a human
    reads; it is never parsed. ``dependency`` names the absent python package
    when — and only when — ``state`` is ``unavailable``, because "install
    pysnmp" and "fix the firewall" are different tickets.
    """

    adapter: str
    fabric: str
    state: str = "unmeasured"
    detail: str = ""
    dependency: str = ""
    checked_at: str = field(default_factory=utcnow)
    #: Source-reported version string, when the source reports one.
    source_version: str = ""

    def __post_init__(self) -> None:
        if self.state not in HEALTH_STATES:
            raise ValueError(
                "unknown health state %r — must be one of %s"
                % (self.state, ", ".join(HEALTH_STATES))
            )

    @property
    def can_discover(self) -> bool:
        return self.state in DISCOVERING_STATES

    @property
    def measured(self) -> bool:
        """True when this state is a statement ABOUT THE SOURCE.

        ``disabled`` / ``unconfigured`` / ``unmeasured`` are statements about
        our own configuration and must never be counted as a source verdict.
        """
        return self.state not in NOT_MEASURED_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "fabric": self.fabric,
            "state": self.state,
            "detail": self.detail,
            "dependency": self.dependency or None,
            "source_version": self.source_version or None,
            "checked_at": self.checked_at,
            "can_discover": self.can_discover,
            "measured": self.measured,
        }


@dataclass
class DiscoveredDevice:
    """One device as reported by one source, in canonical form.

    ``node_id`` is the device's natural key WITHIN its source (a NetBox id, a
    CSV row's hostname, a GNS3 node uuid). It is not globally unique, which is
    why :meth:`stable_id` mixes in the fabric and the adapter — the same
    hostname in two fabrics is two assets, and saying otherwise is precisely
    the identity collapse rmf-ident-01 exists to prevent.
    """

    node_id: str
    label: str = ""
    device_type: str = ""
    vendor: str = ""
    model: str = ""
    firmware_version: str = ""
    serial: str = ""
    site: str = ""
    rack: str = ""
    ip_address: str = ""
    #: Everything the source reported that has no canonical column. Persisted
    #: to ``ni_devices.properties_json`` verbatim.
    properties: dict[str, Any] = field(default_factory=dict)
    adapter: str = ""
    fabric: str = ""
    #: The EVIDENCE CLASS this device is, written to ``ni_devices.source``. It
    #: comes from the adapter CLASS (``DiscoveryAdapter.evidence_source``),
    #: never from the instance id, because a deployment must not be able to
    #: relabel what kind of evidence a source produces by renaming it.
    #: "" means UNKNOWN and is persisted as NULL — see the sink.
    source_label: str = ""
    observed_at: str = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not str(self.node_id).strip():
            raise ValueError("DiscoveredDevice.node_id must be non-empty")
        if not self.label:
            self.label = str(self.node_id)

    def stable_id(self) -> str:
        """Deterministic ni_devices primary key for this (fabric, adapter, node).

        Deterministic so a second discovery run UPDATES the row it wrote last
        time instead of inserting a duplicate. A discovery loop that grows the
        inventory on every pass reports a larger estate every hour and is
        indistinguishable, from the table, from an estate that is actually
        growing.
        """
        raw = "|".join((self.fabric or "", self.adapter or "", str(self.node_id)))
        return "nid-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.stable_id(),
            "node_id": self.node_id,
            "label": self.label,
            "device_type": self.device_type,
            "vendor": self.vendor,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "serial": self.serial,
            "site": self.site,
            "rack": self.rack,
            "ip_address": self.ip_address,
            "adapter": self.adapter,
            "fabric": self.fabric,
            "source_label": self.source_label,
            "observed_at": self.observed_at,
            "properties": dict(self.properties),
        }

    def properties_json(self) -> str:
        """``properties`` plus the provenance every row must carry.

        ``adapter`` / ``fabric`` go in here as well as in dedicated columns
        because ``ni_devices`` on the SQLite fallback has neither a ``source``
        column nor any fabric column — and a row whose origin is unrecoverable
        cannot be told apart from the 24 ``source='synthetic'`` rows already on
        the live board.
        """
        payload = dict(self.properties)
        payload.setdefault("discovery", {})
        payload["discovery"] = {
            "adapter": self.adapter,
            "fabric": self.fabric,
            "source_label": self.source_label,
            "node_id": self.node_id,
            "observed_at": self.observed_at,
            "serial": self.serial,
            "ip_address": self.ip_address,
        }
        return json.dumps(payload, sort_keys=True, default=str)


@dataclass
class DiscoveryResult:
    """What one adapter instance produced on one run."""

    adapter: str
    fabric: str
    health: AdapterHealth
    devices: list[DiscoveredDevice] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: True when ``discover()`` was actually called. ``devices == []`` with
    #: ``discovered`` False is "we never looked", not "the source is empty".
    discovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "fabric": self.fabric,
            "health": self.health.to_dict(),
            "discovered": self.discovered,
            #: None, never 0, when nothing was asked — an unmeasured source and
            #: a measured-empty one justify opposite decisions.
            "device_count": len(self.devices) if self.discovered else None,
            "devices": [d.to_dict() for d in self.devices],
            "errors": list(self.errors),
        }


# ── The contract ──────────────────────────────────────────────────────────────


class DiscoveryAdapter(ABC):
    """One source of asset truth, on one fabric.

    Subclasses set :attr:`name` and implement :meth:`health` and
    :meth:`discover`. ``config`` is the adapter's declaration block from
    ``args/discovery_adapters.yaml`` — no adapter reads the environment or a
    hard-coded endpoint, so a deployment adds a source by editing YAML.
    """

    #: Registry key. Set by every concrete subclass.
    name: str = ""
    #: Python distribution this adapter needs, if any. Reported as
    #: ``AdapterHealth.dependency`` when the import fails.
    requires: str = ""
    #: The EVIDENCE CLASS this source produces, written to ``ni_devices.source``
    #: and read by ``doc_modernization/defacto_learner``'s ``exclude_when``
    #: (rmf-disc-02). It is a property of the KIND of source, so it is a class
    #: attribute and the runner's per-instance rename does not touch it.
    #:
    #: The distinction it carries is the whole reason ni_devices being empty was
    #: HONEST: ``args/docmod/inventory_feeds.yaml`` ranks this table
    #: ``evidence_kind: inventory`` at precedence 10 — an OBSERVED DEPLOYED
    #: ESTATE, outranking every design topology. Once anything writes rows, a
    #: drawn device is physically indistinguishable from an observed one, so
    #: labelling a lab import "discovery" would route a drawing into the
    #: platform's strongest claim about what hardware is fielded.
    evidence_source: str = ""

    def __init__(self, fabric: str = "", config: dict[str, Any] | None = None) -> None:
        self.fabric = fabric or ""
        self.config = dict(config or {})

    # -- helpers every adapter shares -------------------------------------

    def _health(self, state: str, detail: str = "", **kw: Any) -> AdapterHealth:
        return AdapterHealth(
            adapter=self.name, fabric=self.fabric, state=state, detail=detail, **kw
        )

    def _device(self, node_id: str, **kw: Any) -> DiscoveredDevice:
        return DiscoveredDevice(
            node_id=node_id,
            adapter=self.name,
            fabric=self.fabric,
            source_label=self.evidence_source,
            **kw,
        )

    def _missing_config(self, *keys: str) -> list[str]:
        return [k for k in keys if not str(self.config.get(k, "") or "").strip()]

    # -- the contract ------------------------------------------------------

    @abstractmethod
    def health(self) -> AdapterHealth:
        """Ask the source whether it is there. MUST NOT raise.

        An adapter that raises out of ``health()`` turns a source outage into a
        crash of the whole sweep, which loses the health of every OTHER fabric
        — the exact opposite of what a per-fabric report is for. Catch, and
        return ``unreachable`` with the reason in ``detail``.
        """

    @abstractmethod
    def discover(self) -> list[DiscoveredDevice]:
        """Return what the source reports. May raise; the runner records it."""


# ── Registry ──────────────────────────────────────────────────────────────────


class AdapterRegistry:
    """Name -> adapter class. Populated by the package ``__init__``."""

    _adapters: dict[str, type] = {}

    @classmethod
    def register(cls, adapter_cls: type) -> type:
        key = getattr(adapter_cls, "name", "") or ""
        if not key:
            raise ValueError("%s has no `name`" % adapter_cls.__name__)
        cls._adapters[key] = adapter_cls
        return adapter_cls

    @classmethod
    def get(cls, name: str) -> type:
        if name not in cls._adapters:
            raise KeyError(
                "unknown discovery adapter %r. Registered: %s"
                % (name, ", ".join(sorted(cls._adapters)) or "(none)")
            )
        return cls._adapters[name]

    @classmethod
    def create(
        cls, name: str, fabric: str = "", config: dict[str, Any] | None = None
    ) -> DiscoveryAdapter:
        return cls.get(name)(fabric=fabric, config=config)  # type: ignore[return-value]

    @classmethod
    def names(cls) -> list[str]:
        return sorted(cls._adapters)


def probe_dependency(module: str) -> tuple[bool, str]:
    """Is ``module`` importable? Returns ``(ok, detail)``. Never raises.

    ``importlib.util.find_spec("a.b")`` RAISES ``ModuleNotFoundError`` when
    ``a`` itself is absent, so an ``is None`` guard never fires for exactly the
    dependency that is most missing. Import is what the calling code will do,
    so import is what is probed.
    """
    import importlib

    try:
        importlib.import_module(module)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — any import failure is "absent here"
        return False, "%s: %s" % (type(exc).__name__, exc)


#: Type of the callables adapters accept for transport injection in the
#: characterization harness — see ``harness.py``.
Prober = Callable[..., Any]


#: What ``tools/network/discovery.py::_infer_vendor`` returns when NOTHING in
#: the sysDescr matched its 16-entry vendor table. It is a SENTINEL, not a
#: vendor — and the characterization harness is what found it: a mock device
#: reporting "Acme Networks AXOS Software, Version 4.2.1" comes back as
#: ``vendor="Unknown"``, ``device_type="server"``.
UNRECOGNISED_VENDOR = "Unknown"


def normalize_inference(
    device_type: str, vendor: str
) -> tuple[str, str, dict[str, Any]]:
    """Stop a sysDescr-inference SENTINEL becoming an asset attribute.

    ``ni_devices`` is read by ``doc_modernization/defacto_learner`` as OBSERVED
    inventory: it groups by ``vendor``/``model`` and computes a share within the
    feed. Writing the literal string "Unknown" into ``vendor`` therefore
    manufactures a vendor called Unknown with a market share — a fabricated
    fact, produced by a fallback, indistinguishable downstream from a measured
    one.

    ``_infer_device_type`` has the same shape and is worse, because its fallback
    is a REAL type: an unrecognised router is returned as ``"server"``, which no
    reader can tell from an actual server. So when the vendor table recognised
    nothing, the type inferred from that same unrecognised string is not
    reported either.

    Returns ``(device_type, vendor, provenance)``. The RAW inference is kept in
    ``provenance`` — the sentinel is not evidence of a vendor, but it IS
    evidence about the inference, and deleting it would hide that this device
    was seen and not classified.

    The upstream functions are NOT changed: they have other callers-in-waiting
    and a characterization harness that silently repaired what it characterized
    would be measuring its own patch.
    """
    recognised = bool(vendor) and vendor != UNRECOGNISED_VENDOR
    provenance = {
        "vendor_raw": vendor,
        "device_type_raw": device_type,
        "recognised_by_sysdescr_table": recognised,
    }
    if recognised:
        return device_type, vendor, provenance
    return "", "", provenance
