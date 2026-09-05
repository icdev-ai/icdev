# CUI // SP-CTI
"""The ONE Azure-emulator switch for ICDEV (flx-az-01).

A SIBLING OF ``tools/cloud/emulator.py``, NOT A SECOND COPY OF IT
-----------------------------------------------------------------
``emulator.py`` is the AWS seam (floci, port 4566). This is the Azure seam
(floci-az, port 4577). They are separate modules rather than one parameterised
module because **almost nothing about them is shared**, and every difference
below was MEASURED against ``floci/floci-az:0.12.0`` on 2026-09-05 -- see
``docs/spikes/flx-az-parity.md``:

  * different port (4577 vs 4566);
  * different health path -- floci-az answers ``/_floci/health`` and returns
    **501** on ``/_localstack/health``. It is NOT a LocalStack drop-in, so
    there is no ``LOCALSTACK_*`` alias layer to inherit;
  * its health body carries **no ``services`` map** at all, so the AWS seam's
    "which services are running" question has no answer here;
  * a different environment prefix (``FLOCI_AZ_*``);
  * a different region vocabulary (``usgovvirginia``, not ``us-gov-west-1``).

Threading a prefix through the AWS module would put Azure's facts into a
docstring that is load-bearing for AWS callers, and would have to carry a
``services``-map branch that is dead on one side. Two seams, each true.

WHAT THIS MODULE IS
-------------------
A pure configuration seam. It reads environment; it does not start, stop or
configure anything, and NOTHING HERE TOUCHES THE NETWORK AT IMPORT TIME --
``reachable()``, ``health()`` and ``status(probe=True)`` are the only functions
that do, and only when asked.

READ AND INVENTORY ONLY -- THERE IS NO IaC EXECUTION HERE
---------------------------------------------------------
ICDEV ships ``tools/cloud/aws_config_executor.py`` and **no Azure analogue**.
:data:`IAC_EXECUTION_SUPPORTED` is ``False`` and is asserted by a test.
Declaring execution support that no executor backs is the declared-but-
unconsumed defect this platform ships most, and it is refused here explicitly
rather than left to a reader's inference.

THE MEASURED TRAP THIS SEAM EXISTS TO STATE ONCE
------------------------------------------------
:data:`SUBSCRIPTION_SCOPED_LIST_IS_EMPTY`. A subscription-scoped ARM list
returns ``200 {"value":[]}`` for an estate that demonstrably holds resources;
only a RESOURCE-GROUP-scoped list reflects what was written. Measured in one
sequence: ``PUT`` a vnet into ``probe-rg`` (200, real resource id), ``GET`` it
back by id (200), list it in the RG (200, present), list it in the SUBSCRIPTION
(**200, empty**).

Nothing in the status code or body shape distinguishes that from a genuinely
empty subscription. A consumer that lists at subscription scope reports ZERO
for a full estate and looks healthy doing it -- the ``rmf-disc-02`` fabricated
empty exactly. :func:`resource_list_paths` is the one place the fan-out is
spelled; callers use it rather than composing a subscription URL themselves.

THE BANNER IS A CONFIGURATION ECHO
----------------------------------
floci-az prints an ``Enabled Services:`` banner marking 24 services
``[enabled ]``. Measured on two containers -- one with the host socket mounted,
one with ``FLOCI_AZ_DOCKER_DOCKER_HOST`` pointed at a provably absent path --
the banners are otherwise byte-identical, both reporting everything enabled.
It echoes configuration back; it never probed it.

So docker-backing is decided HERE, from :func:`docker_backed`, never from the
emulator's self-report. A caller answering for a container-backed service
without a socket reports ``unsupported_without_docker``, NEVER an empty list --
empty means "no resources", unsupported means "this deployment cannot answer".

Usage::

    from tools.cloud import emulator_az

    if emulator_az.enabled():
        base = emulator_az.endpoint()

    for path in emulator_az.resource_list_paths("virtual_networks", ["rg1"]):
        ...  # one GET per resource group -- never a subscription-scoped list
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable, Mapping, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── The mode name a caller compares against ────────────────────────────────
MODE = "floci-az"

# ── The image ──────────────────────────────────────────────────────────────
#
# PINNED, never ``:latest`` -- an air-gapped rebuild has to be reproducible, and
# a moving tag makes "the image we tested" unanswerable. docker-compose.yml
# carries the same literal for the opt-in `floci-az` profile; YAML cannot import
# a Python constant, so those two are kept in step by hand and a test pins them
# equal. Change both or neither.
IMAGE_REPOSITORY = "floci/floci-az"
IMAGE_TAG = "0.12.0"
IMAGE = f"{IMAGE_REPOSITORY}:{IMAGE_TAG}"

#: Digest MEASURED from the pulled image on 2026-09-05. Recorded so an air-gap
#: bundle can be verified by digest rather than by tag -- a tag-only check reads
#: a `docker load`ed bundle as absent (see the flx-airgap-01 discipline).
IMAGE_DIGEST = "sha256:0c673d49bb75b502ea0750f1c1347777483ffc33945539e1d9254438cb441a03"

#: Port floci-az serves the Azure API edge on, INSIDE the container. The
#: host-side port is a deployment's choice; this one is the emulator's.
CONTAINER_PORT = 4577
DEFAULT_PORT = 4577

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_ENDPOINT = "http://localhost:4577"

#: Azure Government's region name. NOT ``us-gov-west-1`` -- that is AWS's
#: spelling and matches no Azure region. Kept in step with the ``azure_gov``
#: preset in ``args/twin_target_presets.yaml``.
DEFAULT_REGION = "usgovvirginia"

#: The subscription floci-az seeds. Measured from ``GET /subscriptions``.
DEFAULT_SUBSCRIPTION_ID = "00000000-0000-0000-0000-000000000001"

#: The Entra tenant floci-az seeds. Measured from the startup banner and
#: confirmed against its OIDC discovery document.
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000002"

#: floci-az's OWN health path. ``/_localstack/health`` returns **501** here --
#: this emulator is not a LocalStack drop-in and inherits no compat contract.
HEALTH_PATH = "/_floci/health"

#: MEASURED: the health body is ``{"status","edition","version"}`` and carries
#: NO ``services`` map, so this seam cannot answer "which services are up" and
#: does not pretend to. A ``services`` accessor returning ``[]`` would be a
#: fabrication -- the question is unanswerable on this emulator, which is a
#: different finding from "no services are running".
HEALTH_HAS_SERVICE_MAP = False

#: MEASURED: ``/_floci/health`` reports ``"version":"dev"`` while the image's
#: own ``FLOCI_AZ_VERSION`` env var carries ``0.12.0``. Do not source a version
#: claim from the health body.
HEALTH_REPORTS_REAL_VERSION = False

#: ICDEV has no Azure IaC executor. See the module docstring.
IAC_EXECUTION_SUPPORTED = False

#: THE MEASURED TRAP. See the module docstring and
#: ``docs/spikes/flx-az-parity.md`` §1.
SUBSCRIPTION_SCOPED_LIST_IS_EMPTY = True

# ── Proxy port ranges ──────────────────────────────────────────────────────
#
# Host ports floci-az forwards to containers it SPAWNS, read from the startup
# banner on 2026-09-05:
#
#   5000-5099  ACR          (registry:2)
#   6379-6399  Azure Cache  (valkey/valkey:8-alpine)
#   6443-7443  AKS          (rancher/k3s)
#   5672       Event Hubs   AMQP
#   5673       Service Bus  AMQP  -- 5673, NOT 5672; the card said otherwise
#
# DECLARED HERE, DELIBERATELY NOT PUBLISHED BY EVERY CALLER, for the same
# reason as the AWS seam: publishing ~1,100 host ports a deployment cannot
# serve through declares a capability nothing can consume, and collides with a
# local Redis on 6379 and a local registry on 5000. Publish them where a socket
# is actually mounted (the compose profile); everywhere else, honour them by
# keeping the API port out of the ranges.
PROXY_PORT_RANGES: tuple[tuple[int, int], ...] = (
    (5000, 5099),
    (5672, 5673),
    (6379, 6399),
    (6443, 7443),
)


def in_proxy_range(port: int) -> bool:
    """Is ``port`` inside one of floci-az's container-backed proxy ranges?"""
    return any(low <= port <= high for low, high in PROXY_PORT_RANGES)


# ── status() values ────────────────────────────────────────────────────────
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_UNREACHABLE = "unreachable"
STATUS_DEGRADED_NO_DOCKER = "degraded_no_docker"

#: What a container-backed logical table must report when no socket is mounted.
#: NEVER an empty list -- see the module docstring.
UNSUPPORTED_WITHOUT_DOCKER = "unsupported_without_docker"

#: Azure services floci-az backs with a SPAWNED CONTAINER, from the startup
#: banner. ``aci`` and ``vm`` are deliberately ABSENT: measured, they report
#: ``docker: mocked  (no docker)`` even WITH the socket mounted, so they are
#: not container-backed in practice and refusing them without a socket would be
#: a fabricated refusal.
#:
#: Note the measured scope limit: the ARM MANAGEMENT lane for these services
#: answers identically with and without a socket (listing metadata spawns
#: nothing). A socket is needed to START one, not to list them -- so a caller
#: listing inventory must NOT refuse on this set. It is
#: :func:`data_plane_supported` that consults it.
CONTAINER_BACKED_SERVICES = frozenset(
    {
        "functions",
        "aks",
        "acr",
        "redis",
        "eventhub",
        "servicebus",
    }
)

#: Services the banner marks ``[enabled ]`` that NO route reached on 2026-09-05.
#: Recorded BY NAME rather than merged into "absent", because the repairs
#: differ: find the route / file upstream, versus do not design against it.
#: Nothing in this tree may declare a capability over one of these.
DECLARED_UNREACHABLE_SERVICES = frozenset({"appconfig", "eventgrid", "functions", "monitor"})

# ── ARM resource types this seam will list ─────────────────────────────────
#
# Every entry ANSWERED (HTTP 200) on 2026-09-05, and the control probes
# (Microsoft.Nonsense/widgets, Contoso.Fake/things, Microsoft.Quantum/
# workspaces) all returned 404 -- so a 200 here discriminates a registered
# provider from an unregistered one rather than being a blanket accept.
#
# Providers measured 404 and therefore ABSENT on purpose: Microsoft.Web/sites,
# EventHub, ServiceBus, EventGrid/topics, Insights/components,
# OperationalInsights/workspaces, ApiManagement/service, AppConfiguration,
# DocumentDB. Adding one back needs a re-measurement, not an api-version guess.
#
# table name -> (provider/type path, api-version)
ARM_RESOURCE_TYPES: dict[str, tuple[str, str]] = {
    "virtual_networks": ("Microsoft.Network/virtualNetworks", "2023-05-01"),
    "network_security_groups": ("Microsoft.Network/networkSecurityGroups", "2023-05-01"),
    "storage_accounts": ("Microsoft.Storage/storageAccounts", "2023-01-01"),
    "key_vaults": ("Microsoft.KeyVault/vaults", "2023-02-01"),
    "managed_identities": (
        "Microsoft.ManagedIdentity/userAssignedIdentities",
        "2023-01-31",
    ),
}

#: api-version for the resource-group and generic-resource lanes.
RESOURCES_API_VERSION = "2021-04-01"


# ── env reading ────────────────────────────────────────────────────────────


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def _source(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _read(name: str, env: Optional[Mapping[str, str]] = None, *, default: str = "") -> str:
    """Read ``FLOCI_AZ_<X>``.

    NO ALIAS LAYER, deliberately. The AWS seam honours ``LOCALSTACK_*`` because
    floci translates those names itself and ICDEV had already emitted them into
    customer compose files. Neither is true here: floci-az is not a LocalStack
    drop-in (``/_localstack/health`` -> 501) and ICDEV has never emitted an
    Azure emulator variable. Inventing an alias would create the deprecation
    debt rather than absorb it.

    An empty string is treated as unset -- an operator who wrote
    ``FLOCI_AZ_ENDPOINT=`` has not declared an endpoint.
    """
    val = _source(env).get(name)
    return val if val else default


# ── The switch ─────────────────────────────────────────────────────────────


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is the Azure emulator switched ON for this deployment?

    ``FLOCI_AZ_ENABLED``, default **false**. Air-gap-safe: an operator opts in
    explicitly, and a deployment that configures nothing reaches no emulator.

    Deliberately INDEPENDENT of ``FLOCI_ENABLED`` (the AWS seam). A deployment
    running the AWS emulator has said nothing about wanting an Azure one, and
    coupling them would start a second 305 MB JVM container nobody asked for.
    """
    return _truthy(_read("FLOCI_AZ_ENABLED", env, default="false"))


def endpoint(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator base URL, trailing slash stripped. Default ``http://localhost:4577``."""
    return _read("FLOCI_AZ_ENDPOINT", env, default=DEFAULT_ENDPOINT).rstrip("/")


def endpoint_declared(env: Optional[Mapping[str, str]] = None) -> bool:
    """Did the operator explicitly configure an emulator endpoint?

    Distinct from :func:`endpoint`, which always answers (with the default).
    This is what tells a CONFIGURED emulator apart from an ASSUMED one.
    """
    return bool(_source(env).get("FLOCI_AZ_ENDPOINT"))


def region(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator region. Default ``usgovvirginia`` -- ICDEV's Azure partition."""
    return _read("FLOCI_AZ_REGION", env, default=DEFAULT_REGION)


def subscription_id(env: Optional[Mapping[str, str]] = None) -> str:
    """Subscription id ARM calls are scoped to.

    A malformed value logs one line and falls back rather than raising -- a
    getter that raises turns a typo into an unhandled exception inside whatever
    swallowing handler surrounds it. Note the consequence: two deployments that
    both mis-set it share emulator state. Fix the value.
    """
    raw = _read("FLOCI_AZ_SUBSCRIPTION_ID", env, default=DEFAULT_SUBSCRIPTION_ID).strip()
    if _looks_like_guid(raw):
        return raw
    _warn_once(
        "FLOCI_AZ_SUBSCRIPTION_ID:invalid",
        "FLOCI_AZ_SUBSCRIPTION_ID=%r is not a GUID; falling back to %s.",
        raw,
        DEFAULT_SUBSCRIPTION_ID,
    )
    return DEFAULT_SUBSCRIPTION_ID


def tenant_id(env: Optional[Mapping[str, str]] = None) -> str:
    """Entra tenant id the emulator issues tokens for."""
    raw = _read("FLOCI_AZ_TENANT_ID", env, default=DEFAULT_TENANT_ID).strip()
    if _looks_like_guid(raw):
        return raw
    _warn_once(
        "FLOCI_AZ_TENANT_ID:invalid",
        "FLOCI_AZ_TENANT_ID=%r is not a GUID; falling back to %s.",
        raw,
        DEFAULT_TENANT_ID,
    )
    return DEFAULT_TENANT_ID


_GUID_SEGMENTS = (8, 4, 4, 4, 12)


def _looks_like_guid(value: str) -> bool:
    parts = value.split("-")
    if len(parts) != len(_GUID_SEGMENTS):
        return False
    return all(
        len(part) == width and all(c in "0123456789abcdefABCDEF" for c in part)
        for part, width in zip(parts, _GUID_SEGMENTS)
    )


_WARNED: set[str] = set()


def reset_warnings() -> None:
    """Forget which one-shot warnings have fired. For tests; not a runtime path."""
    _WARNED.clear()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(message, *args)


# ── ARM path construction ──────────────────────────────────────────────────


def subscription_path(env: Optional[Mapping[str, str]] = None) -> str:
    """``/subscriptions/<id>`` -- the root every ARM path below hangs off."""
    return f"/subscriptions/{subscription_id(env)}"


def resource_groups_path(env: Optional[Mapping[str, str]] = None) -> str:
    """List path for resource groups.

    Subscription-scoped AND MEASURED TO WORK -- it returned ``probe-rg`` after a
    ``PUT``. That is what makes the per-RG fan-out below possible at all, and it
    is the ONE subscription-scoped list this seam will compose.
    """
    return f"{subscription_path(env)}/resourcegroups?api-version={RESOURCES_API_VERSION}"


def resource_list_paths(
    table: str,
    resource_groups: Iterable[str],
    env: Optional[Mapping[str, str]] = None,
) -> list[str]:
    """One ARM list path PER RESOURCE GROUP for *table*. Never subscription-scoped.

    THE WHOLE POINT OF THIS FUNCTION IS THE SCOPE, and it is stated once here so
    no caller has to remember it. A subscription-scoped list returns
    ``200 {"value":[]}`` for a populated estate (measured 2026-09-05; see
    ``docs/spikes/flx-az-parity.md`` §1), and nothing about that response
    distinguishes it from a genuinely empty subscription.

    An EMPTY ``resource_groups`` yields an EMPTY list of paths -- and a caller
    must read that as "nothing to ask", never as "the estate is empty". The two
    are told apart by whether the resource-group enumeration itself succeeded,
    which is the caller's to track; :func:`resource_list_paths` cannot know.

    :raises KeyError: if *table* is not a measured-answering ARM type.
    """
    provider, api_version = ARM_RESOURCE_TYPES[table]
    base = subscription_path(env)
    return [
        f"{base}/resourcegroups/{rg}/providers/{provider}?api-version={api_version}"
        for rg in resource_groups
    ]


def generic_resources_path(
    resource_group: str, env: Optional[Mapping[str, str]] = None
) -> str:
    """Every resource in ONE resource group. Subscription scope is empty; this is not."""
    return (
        f"{subscription_path(env)}/resourcegroups/{resource_group}"
        f"/resources?api-version={RESOURCES_API_VERSION}"
    )


# ── Docker socket ──────────────────────────────────────────────────────────
#
# Basis values reported by docker_basis(). Each names a DIFFERENT question the
# caller may need to ask next, so they are never merged.
BASIS_DECLARED_REMOTE = "declared_remote_daemon"
BASIS_SOCKET_PRESENT = "unix_socket_present"
BASIS_SOCKET_ABSENT = "unix_socket_absent"
BASIS_UNKNOWN_NAMED_PIPE = "unknown_windows_named_pipe"
BASIS_DECLARED_UNPARSED = "declared_unparsed"

_DEFAULT_UNIX_SOCKET = "/var/run/docker.sock"
_REMOTE_SCHEMES = ("tcp://", "ssh://", "npipe://", "http://", "https://")


def _declared_socket(env: Optional[Mapping[str, str]] = None) -> str:
    src = _source(env)
    return (src.get("FLOCI_AZ_DOCKER_SOCKET") or src.get("DOCKER_HOST") or "").strip()


def docker_basis(env: Optional[Mapping[str, str]] = None) -> str:
    """How :func:`docker_backed` reached its answer. See the ``BASIS_*`` constants."""
    declared = _declared_socket(env)
    if declared:
        if declared.startswith(_REMOTE_SCHEMES):
            return BASIS_DECLARED_REMOTE
        if declared.startswith("unix://"):
            path = declared[len("unix://") :]
            return BASIS_SOCKET_PRESENT if Path(path).exists() else BASIS_SOCKET_ABSENT
        if declared.startswith("/"):
            return BASIS_SOCKET_PRESENT if Path(declared).exists() else BASIS_SOCKET_ABSENT
        return BASIS_DECLARED_UNPARSED
    if sys.platform == "win32":
        return BASIS_UNKNOWN_NAMED_PIPE
    return BASIS_SOCKET_PRESENT if Path(_DEFAULT_UNIX_SOCKET).exists() else BASIS_SOCKET_ABSENT


def docker_backed(env: Optional[Mapping[str, str]] = None) -> Optional[bool]:
    r"""Is a docker socket mounted for this deployment? ``True | False | None``.

    TRI-STATE, and ``None`` (cannot tell) is the point. A Windows named pipe is
    not reliably stat-able -- measured on this host, Docker Desktop 28.5.1 was
    RUNNING and ``os.path.exists(r"\\.\pipe\docker_engine")`` returned False --
    so Windows-with-no-``DOCKER_HOST`` is ``None``, never ``False``. Returning
    ``False`` there would be a fabricated refusal for a working daemon.

    ``None`` is NOT ``False``: callers must compare explicitly. The rule is
    "refuse only what is PROVEN unavailable".
    """
    basis = docker_basis(env)
    if basis in (BASIS_DECLARED_REMOTE, BASIS_SOCKET_PRESENT):
        return True
    if basis == BASIS_SOCKET_ABSENT:
        return False
    return None


def data_plane_supported(service: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Can this deployment reach *service*'s DATA plane?

    ``False`` ONLY when the service is container-backed AND the socket is
    PROVEN absent. An unknown socket permits the call: the emulator's own error
    is better evidence than our guess.

    NOT THE SAME QUESTION AS LISTING INVENTORY, and the difference is measured.
    The ARM MANAGEMENT lane for every container-backed service answered
    IDENTICALLY with and without a socket -- listing metadata spawns no
    container. So an inventory reader must NOT consult this; only a caller
    about to reach a data plane (invoke a function, connect to a cache) should.
    """
    return (
        service.strip().lower() not in CONTAINER_BACKED_SERVICES
        or docker_backed(env) is not False
    )


# ── Reachability / status ──────────────────────────────────────────────────


def _health_request(env: Optional[Mapping[str, str]]) -> tuple[str, urllib.request.Request]:
    url = f"{endpoint(env)}{HEALTH_PATH}"
    return url, urllib.request.Request(  # noqa: S310 -- operator-configured endpoint
        url, headers={"Accept": "application/json", "User-Agent": "ICDEV-EmulatorAz/1.0"}
    )


def reachable(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> bool:
    """Does the emulator answer its health endpoint? Costs one HTTP GET.

    A 501 is NOT reachable-and-healthy. floci-az routes any unknown path into
    its blob handler, which answers 501 ``NotImplemented`` rather than 404 --
    so "did not 404" proves nothing here, and ``urlopen`` raising on the 501 is
    exactly the behaviour wanted.
    """
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            resp.read()
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("azure emulator health probe failed for %s: %s", url, exc)
        return False


def health(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> dict:
    """Parsed ``/_floci/health`` body, or ``{}`` when it could not be read.

    ``{}`` here means "not read", never "no services" -- read it beside
    :func:`status`, which is what says which.

    MEASURED shape: ``{"status": "UP", "edition": "floci-az-always-free",
    "version": "dev"}``. There is NO ``services`` key
    (:data:`HEALTH_HAS_SERVICE_MAP` is False) and ``version`` does not carry the
    real release (:data:`HEALTH_REPORTS_REAL_VERSION` is False).
    """
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("azure emulator health read failed for %s: %s", url, exc)
        return {}


def status(
    env: Optional[Mapping[str, str]] = None, *, probe: bool = True, timeout: float = 2.0
) -> str:
    """One of ``disabled | unreachable | degraded_no_docker | enabled``.

    Ordered by severity, and the order is the design:

      ``disabled``            the switch is off. Says nothing about the host.
      ``unreachable``         switched on, nothing answers at the endpoint.
      ``degraded_no_docker``  switched on and answering, but the socket is
                              PROVEN absent, so container-backed DATA planes
                              (Functions, AKS, ACR, Cache, Event Hubs, Service
                              Bus) cannot be reached. The ARM inventory lane is
                              unaffected -- measured.
      ``enabled``             switched on, answering, socket present or unproven.

    ``probe=False`` answers without touching the network.
    """
    if not enabled(env):
        return STATUS_DISABLED
    if probe and not reachable(env, timeout=timeout):
        return STATUS_UNREACHABLE
    if docker_backed(env) is False:
        return STATUS_DEGRADED_NO_DOCKER
    return STATUS_ENABLED
