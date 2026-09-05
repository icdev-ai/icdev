# CUI // SP-CTI
"""The ONE OCI-emulator switch for ICDEV (flx-oci-01).

A FOURTH SIBLING, NOT A SECOND COPY
-----------------------------------
``emulator.py`` is the AWS seam (floci, 4566), ``emulator_az.py`` the Azure one
(floci-az, 4577), ``emulator_gcp.py`` the GCP one (floci-gcp, 4588). This is the
OCI seam (floci-oci, 4599). Everything below was MEASURED against
``floci/floci-oci:0.4.0`` on 2026-09-05 -- see ``docs/spikes/flx-oci-parity.md``.

READ THIS FIRST: NOTHING IN ICDEV CAN CONSUME THIS SEAM'S ENDPOINT YET
----------------------------------------------------------------------
The card asked for a measurement before any promise, because floci-oci is the
youngest of the four. The measurement found the risk on the OTHER side.

The emulator is fine: eight services, every REST lane answering, every write
reflected, ``compartmentId`` honoured, controls discriminating. What does not
work is **ICDEV's own OCI provider layer**, and it does not work in a way no
endpoint can repair -- :data:`PROVIDER_LAYER_IS_STUBBED`:

  * ``OCIObjectStorageProvider.list_objects`` is ``return []``. ``upload`` is
    ``return False  # Requires full OCI config``. There is no network call in
    the class at all, so there is no socket for an endpoint to redirect.
  * ``OCISecretsProvider``, ``OCIKMSProvider`` and ``OCIIAMProvider`` are the
    same shape -- constants, not calls.
  * Exactly TWO sites in the tree pass a ``service_endpoint``
    (:data:`ICDEV_ENDPOINT_HONOURING_SITES`), both in the LLM stack, and both
    target Generative AI inference -- which this emulator answers **404** on
    every path (:data:`GENERATIVE_AI_IS_ABSENT`).
  * The ``oci`` SDK is neither installed nor in ``requirements.txt``, so every
    provider's ``_HAS_OCI_*`` guard is False regardless.

So this seam ships to serve the DataBridge connector and the Twin adapter --
which read over plain ``urllib`` through the governed broker, and are NEW
consumers rather than existing OCI paths. It deliberately exports **no**
client-config helper, because handing ``tools/cloud/*_provider.py`` an endpoint
would declare a capability whose first call returns ``[]``. Repairing that is a
follow-on card that implements the providers; more emulator surface will not
help.

WHAT THIS MODULE IS
-------------------
A pure configuration seam. It reads environment; it does not start, stop or
configure anything, and NOTHING HERE TOUCHES THE NETWORK AT IMPORT TIME --
:func:`reachable`, :func:`health`, :func:`namespace_probed` and
``status(probe=True)`` are the only functions that do, and only when asked.

READ AND INVENTORY ONLY -- THERE IS NO IaC EXECUTION HERE
---------------------------------------------------------
ICDEV ships ``tools/cloud/aws_config_executor.py`` and **no OCI analogue**.
:data:`IAC_EXECUTION_SUPPORTED` is ``False`` and is asserted by a test,
including an AST check that this module imports no ``subprocess`` and no
``docker``.

THE MEASURED TRAPS THIS SEAM EXISTS TO STATE ONCE
-------------------------------------------------
1. :data:`SERVICE_LIST_SELF_REPORTS_DISAGREE`. The container publishes its
   service set twice and the two differ: the startup ``ServiceRegistry`` log
   line names SEVEN, ``/health`` names EIGHT. The extra one is ``functions``,
   and measured, functions WORKS. The log line -- the obvious thing to grep,
   and what the floci-az seam had to use because that emulator has no map -- is
   the incomplete one. :data:`SERVICES` comes from the measured lanes instead.

2. :data:`HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY`. ``/health`` publishes eight
   services, every one reading ``"running"``. Measured on two containers, one
   with the socket mounted and one with it pointed at a provably absent path,
   **the bodies are byte-identical**. It is a registry listing wearing a
   measurement's name -- the same defect as floci-gcp's 23-service map.

3. :data:`FABRICATED_ACTIVE_WITH_DOCKER`, and note that it is the MIRROR of the
   GCP sibling's constant rather than a copy of it. floci-gcp needed
   ``FABRICATED_SUCCESS_WITHOUT_DOCKER = {"cloudrun"}``. Here, WITHOUT a socket
   OKE fails honestly (500, nothing recorded) -- so that set is
   **measured empty**. The fabrication happens WITH a socket: the emulator
   spawns ``rancher/k3s:v1.30.1-k3s1``, k3s dies immediately
   (``--token is required`` -- floci-oci never passes one), and the API keeps
   reporting ``lifecycleState: ACTIVE`` with a ``kubernetes`` endpoint that has
   no listener. OKE cannot work at all in 0.4.0.
   :data:`OKE_LIFECYCLE_IS_UNVERIFIED` is why no consumer may promote that
   field to a health verdict.

4. :data:`RESPONSE_ENDPOINTS_ARE_CONTAINER_LOCAL`. A resource body advertises
   ``http://localhost:4599`` -- the CONTAINER's port, hard-coded -- so a client
   that follows an endpoint out of a response goes to the wrong place on any
   non-default mapping. URLs are composed from :func:`endpoint`, never from a
   response field.

5. :data:`RESPONSE_ROW_KEY`. ``/20210201/queues`` wraps its rows in
   ``{"items": [...]}``; the other eight lanes return a **bare list**. Assuming
   either shape is wrong about the other, so :func:`rows_from` is the one place
   the distinction lives.

WHAT THIS EMULATOR DOES *NOT* HAVE, and no sibling seam may lend it
-------------------------------------------------------------------
No floci-az subscription-scope trap: ``compartmentId`` is honoured, measured
against a bogus compartment returning 0 rows for four services. No floci-gcp
gRPC-only blind spot: every declared service answers REST. No proxy port
ranges. And no Compute, Networking, Database or Load Balancer surface at all --
eight services is the whole emulator, and the controls 404.

Usage::

    from tools.cloud import emulator_oci

    if emulator_oci.enabled() and emulator_oci.reachable():
        url = emulator_oci.resource_url("vaults")
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── The mode name a caller compares against ────────────────────────────────
MODE = "floci-oci"

# ── Image identity ─────────────────────────────────────────────────────────
IMAGE_REPOSITORY = "floci/floci-oci"
IMAGE_TAG = "0.4.0"
IMAGE = f"{IMAGE_REPOSITORY}:{IMAGE_TAG}"

#: Digest measured on 2026-09-05. The air-gap cache is keyed on THIS, never on
#: the tag -- a tag-only check reports a fabricated hit for a bundle loaded by
#: digest.
IMAGE_DIGEST = "sha256:584fd7f977077ab040063d7c2efaaaa1beabacccd903f5297eaa7bbe8f744a8b"

# ── Network ────────────────────────────────────────────────────────────────
CONTAINER_PORT = 4599
DEFAULT_PORT = 4599
DEFAULT_ENDPOINT = "http://localhost:4599"

#: The emulator's OWN default region, measured from its startup banner. NOT
#: ICDEV's ``us-gov-west-1`` default: that is an AWS region name and means
#: nothing to OCI. The OCI government regions are declared in
#: ``args/twin_target_presets.yaml``, not here.
DEFAULT_REGION = "us-ashburn-1"

#: Object Storage namespace the emulator seeds. Unlike floci-gcp's project id,
#: this one IS discoverable at run time -- see :func:`namespace_probed`.
DEFAULT_NAMESPACE = "floci-local"

#: Tenancy OCID the emulator seeds. Every list lane is compartment-scoped and
#: this is the root compartment, so it is the default ``compartmentId``.
DEFAULT_TENANCY_OCID = (
    "ocid1.tenancy.oc1..flocilocaltenancy0000000000000000000000000000000000000000"
)

# ── Health ─────────────────────────────────────────────────────────────────
#: Third emulator, third path. floci answers ``/_localstack/health``, floci-az
#: ``/_floci/health``, this one ``/health`` -- and it 404s on BOTH of the
#: others, so it is NOT a LocalStack drop-in. ``/q/health`` and ``/healthz``
#: also 404: the Quarkus management surface is not exposed.
HEALTH_PATH = "/health"

#: ``/health`` carries a ``services`` map that parses.
HEALTH_HAS_SERVICE_MAP = True

#: ...and it is NOT a health signal. Byte-identical on a container whose docker
#: socket points at a provably absent path, every value the literal
#: ``"running"``. Kept as a SECOND constant so no consumer can read the first
#: one as permission to render a status badge.
HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY = True

#: ``/health`` reports the real version string (``0.4.0``), unlike floci-az.
HEALTH_REPORTS_REAL_VERSION = True

#: The startup ``ServiceRegistry`` log line names 7 services; ``/health`` names
#: 8. The extra one is ``functions`` and it ANSWERS -- so the log line is the
#: incomplete report. Stated once so a future card does not enumerate services
#: by grepping the banner.
SERVICE_LIST_SELF_REPORTS_DISAGREE = True
SERVICE_LIST_LOG_LINE_OMITS = frozenset({"functions"})

# ── Capability refusals, each measured ─────────────────────────────────────
#: ICDEV has ``aws_config_executor.py`` and no OCI analogue.
IAC_EXECUTION_SUPPORTED = False

#: The §1 finding, in code. ICDEV's OCI providers return constants, not calls,
#: so no endpoint can reach them. See this module's docstring.
PROVIDER_LAYER_IS_STUBBED = True

#: How many ICDEV sites pass a ``service_endpoint`` to an OCI client. Both are
#: in the LLM stack and both target Generative AI inference.
ICDEV_ENDPOINT_HONOURING_SITES = 2

#: ...which this emulator does not serve: 404 on every generative-AI path
#: tried. So the two endpoint-honouring sites cannot use it either.
GENERATIVE_AI_IS_ABSENT = True

#: A resource body advertises the CONTAINER's port, hard-coded, so it is wrong
#: on any non-default host mapping. Compose URLs from :func:`endpoint`.
RESPONSE_ENDPOINTS_ARE_CONTAINER_LOCAL = True

# ── Status vocabulary ──────────────────────────────────────────────────────
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_UNREACHABLE = "unreachable"
STATUS_DEGRADED_NO_DOCKER = "degraded_no_docker"

#: What a docker-backed table reports when the socket is PROVEN absent. Never
#: an empty list -- that would assert the estate holds nothing.
UNSUPPORTED_WITHOUT_DOCKER = "unsupported_without_docker"

# ── Services ───────────────────────────────────────────────────────────────
#: The eight services taken from MEASURED lanes, not from either self-report.
SERVICES = frozenset(
    {
        "identity",
        "kms",
        "objectstorage",
        "oke",
        "queue",
        "streaming",
        "vault",
        "functions",
    }
)

#: The only service that starts a container. Measured by what actually spawned.
CONTAINER_BACKED_SERVICES = frozenset({"oke"})

#: The image OKE spawns. Version-pinned, unlike floci-gcp's two ``:latest``
#: tags, so an air-gap set built from it is enumerable by digest.
CONTAINER_BACKED_IMAGES: dict[str, str] = {"oke": "rancher/k3s:v1.30.1-k3s1"}

#: MEASURED EMPTY, not omitted, and the difference matters. floci-gcp's Cloud
#: Run returns a fabricated 200 with no socket; floci-oci's OKE returns an
#: honest 500 and records nothing. Do not copy the sibling's set here.
FABRICATED_SUCCESS_WITHOUT_DOCKER: frozenset[str] = frozenset()

#: The fabrication floci-oci DOES have, and it is the mirror of the above: WITH
#: a socket, OKE spawns k3s, k3s dies (``--token is required``), and the API
#: still reports ``lifecycleState: ACTIVE`` with a dead endpoint.
FABRICATED_ACTIVE_WITH_DOCKER = frozenset({"oke"})

#: So an OKE row is never evidence of a working cluster, whatever it says.
OKE_LIFECYCLE_IS_UNVERIFIED = True

# ── REST lanes ─────────────────────────────────────────────────────────────
#: Every path here returned 200 with real data on 2026-09-05. A service absent
#: from this map is absent because it was MEASURED absent, and composing a
#: plausible path for it would hand a caller a URL that 404s -- which reads as
#: "no such resource" rather than "no such service".
REST_RESOURCE_PATHS: dict[str, str] = {
    "buckets": "/n/{namespace}/b/?compartmentId={compartment}",
    "compartments": "/20160918/compartments?compartmentId={compartment}",
    "users": "/20160918/users?compartmentId={compartment}",
    "groups": "/20160918/groups?compartmentId={compartment}",
    "policies": "/20160918/policies?compartmentId={compartment}",
    "vaults": "/20180608/vaults?compartmentId={compartment}",
    "keys": "/20180608/keys?compartmentId={compartment}",
    "queues": "/20210201/queues?compartmentId={compartment}",
    "streams": "/20180418/streams?compartmentId={compartment}",
    "applications": "/20181201/applications?compartmentId={compartment}",
    "clusters": "/20180222/clusters?compartmentId={compartment}",
}

#: Which service each table belongs to -- the docker-backing question is asked
#: per SERVICE, and only ``clusters`` maps to a container-backed one.
TABLE_SERVICE: dict[str, str] = {
    "buckets": "objectstorage",
    "compartments": "identity",
    "users": "identity",
    "groups": "identity",
    "policies": "identity",
    "vaults": "vault",
    "keys": "kms",
    "queues": "queue",
    "streams": "streaming",
    "applications": "functions",
    "clusters": "oke",
}

#: Row envelope per lane. ``queues`` is the ONLY wrapped one; ``None`` means the
#: body IS the list. Measured, not assumed -- both shapes are live on this one
#: emulator and a uniform reader is wrong about one of them.
RESPONSE_ROW_KEY: dict[str, Optional[str]] = {
    "buckets": None,
    "compartments": None,
    "users": None,
    "groups": None,
    "policies": None,
    "vaults": None,
    "keys": None,
    "queues": "items",
    "streams": None,
    "applications": None,
    "clusters": None,
}

#: Lanes that REFUSE (400) when ``compartmentId`` is omitted, while others
#: answer 200. Recorded because a caller must not generalise from one lane.
COMPARTMENT_REQUIRED_LANES = frozenset({"vaults", "buckets"})

# ── Persistence ────────────────────────────────────────────────────────────
#: The switch that actually works. THREE plausible spellings are silently
#: ignored (``FLOCI_OCI_PERSISTENCE``, ``FLOCI_PERSISTENCE``,
#: ``FLOCI_OCI_PERSISTENCE_MODE``) -- an operator who used one of those
#: believes they enabled persistence and did not.
STORAGE_MODE_VAR = "FLOCI_OCI_STORAGE_MODE"
DEFAULT_STORAGE_MODE = "memory"

#: Only these two were EXERCISED. The card also claims ``hybrid`` and ``wal``;
#: they were not measured, so they are not enumerated as supported here.
MEASURED_STORAGE_MODES = frozenset({"memory", "persistent"})


# ── Helpers ────────────────────────────────────────────────────────────────


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def _source(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _read(name: str, env: Optional[Mapping[str, str]] = None, *, default: str = "") -> str:
    """Read ``FLOCI_OCI_<X>``.

    NO ALIAS LAYER, deliberately -- the same reasoning as the Azure and GCP
    seams. The AWS seam honours ``LOCALSTACK_*`` because floci translates those
    names itself and ICDEV had already emitted them into compose files it does
    not control. Neither is true here.

    An empty string is treated as unset -- an operator who wrote
    ``FLOCI_OCI_ENDPOINT=`` has not declared an endpoint.
    """
    val = _source(env).get(name)
    return val if val else default


_WARNED: set[str] = set()


def reset_warnings() -> None:
    """Clear the once-only warning ledger. For tests."""
    _WARNED.clear()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message, *args)


# ── The switch ─────────────────────────────────────────────────────────────


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is the OCI emulator switched ON for this deployment?

    ``FLOCI_OCI_ENABLED``, default **false**. Air-gap-safe: an operator opts in
    explicitly, and a deployment that configures nothing reaches no emulator.

    Deliberately INDEPENDENT of the other three switches. A deployment running
    one emulator has said nothing about wanting a fourth.
    """
    return _truthy(_read("FLOCI_OCI_ENABLED", env, default="false"))


def endpoint(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator base URL, trailing slash stripped. Default ``http://localhost:4599``."""
    return _read("FLOCI_OCI_ENDPOINT", env, default=DEFAULT_ENDPOINT).rstrip("/")


def endpoint_declared(env: Optional[Mapping[str, str]] = None) -> bool:
    """Did the operator explicitly configure an endpoint?

    Distinct from :func:`endpoint`, which always answers (with the default).
    This is what tells a CONFIGURED emulator apart from an ASSUMED one.
    """
    return bool(_source(env).get("FLOCI_OCI_ENDPOINT"))


def region(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator region. Default ``us-ashburn-1``.

    Honoured by the emulator, measured: ``us-phoenix-1`` changes the banner AND
    the region code embedded in every OCID it mints (``…oc1.phx.…`` rather than
    ``…oc1.iad.…``).
    """
    return _read("FLOCI_OCI_REGION", env, default=DEFAULT_REGION)


def storage_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """Declared persistence mode. Default ``memory``.

    Reads the ONE variable measured to work. A value outside
    :data:`MEASURED_STORAGE_MODES` is returned unchanged with one warning: it
    may well work, it simply was not exercised, and silently rewriting it to
    ``memory`` would be worse than passing it through.
    """
    mode = _read(STORAGE_MODE_VAR, env, default=DEFAULT_STORAGE_MODE).strip().lower()
    if mode not in MEASURED_STORAGE_MODES:
        _warn_once(
            f"{STORAGE_MODE_VAR}:unmeasured",
            "%s=%r was never exercised by flx-oci-01; passing it through unchanged.",
            STORAGE_MODE_VAR,
            mode,
        )
    return mode


def namespace(env: Optional[Mapping[str, str]] = None) -> str:
    """Object Storage namespace, from configuration. Never touches the network.

    See :func:`namespace_probed` for the discovered value.
    """
    raw = _read("FLOCI_OCI_NAMESPACE", env, default=DEFAULT_NAMESPACE).strip()
    if _path_safe(raw):
        return raw
    _warn_once(
        "FLOCI_OCI_NAMESPACE:invalid",
        "FLOCI_OCI_NAMESPACE=%r is not path-safe; falling back to %s.",
        raw,
        DEFAULT_NAMESPACE,
    )
    return DEFAULT_NAMESPACE


def compartment_id(env: Optional[Mapping[str, str]] = None) -> str:
    """The ``compartmentId`` every list lane is scoped to.

    Defaults to the tenancy (the root compartment) the emulator seeds. ALWAYS
    ANSWERS: every lane but two requires the parameter, and a caller with none
    has nothing to fall back on.
    """
    raw = _read(
        "FLOCI_OCI_COMPARTMENT_OCID",
        env,
        default=_read("FLOCI_OCI_TENANCY_OCID", env, default=DEFAULT_TENANCY_OCID),
    ).strip()
    if _path_safe(raw):
        return raw
    _warn_once(
        "FLOCI_OCI_COMPARTMENT_OCID:invalid",
        "FLOCI_OCI_COMPARTMENT_OCID=%r is not safe to interpolate; falling back to the tenancy.",
        raw,
    )
    return DEFAULT_TENANCY_OCID


def tenancy_id(env: Optional[Mapping[str, str]] = None) -> str:
    """The tenancy OCID. Default is the one the emulator seeds."""
    raw = _read("FLOCI_OCI_TENANCY_OCID", env, default=DEFAULT_TENANCY_OCID).strip()
    return raw if _path_safe(raw) else DEFAULT_TENANCY_OCID


def _path_safe(value: str) -> bool:
    """Is *value* safe to interpolate into a URL path or query?

    Defence in depth between an env var and a composed URL. A value carrying
    ``/``, ``?``, ``#`` or whitespace would compose a request pointing somewhere
    else entirely. :func:`resource_path` ALSO percent-encodes with
    ``quote(safe="")``, so this is the second guard rather than the only one.

    ``..`` IS PERMITTED, and that is not an oversight. Every OCID contains it --
    ``ocid1.tenancy.oc1..flocilocaltenancy0000…`` -- because the region segment
    is empty for global resources, so a rule that rejected ``..`` would refuse
    the emulator's own default tenancy and every id a caller could supply. Path
    traversal needs a SEPARATOR, and ``/`` and ``\\`` are both refused above, so
    ``..`` alone cannot climb anywhere.

    Deliberately a SAFETY predicate rather than an OCID grammar: the emulator
    mints its own ids and rejecting a well-formed id we failed to anticipate
    would be worse than permitting an odd-looking one.
    """
    if not value or len(value) > 255:
        return False
    if any(c.isspace() for c in value):
        return False
    return not any(bad in value for bad in ("/", "?", "#", "\\", "%"))


# ── Path composition ───────────────────────────────────────────────────────


def resource_path(table: str, env: Optional[Mapping[str, str]] = None) -> str:
    """The REST path for *table*, namespace and compartment substituted.

    The ONE place a path is composed.

    :raises KeyError: if *table* is not a measured-answering lane. Deliberate:
        returning a plausible path for a service this emulator does not have
        would hand a caller a URL that 404s, and a 404 reads as "no such
        resource" rather than "no such service".
    """
    template = REST_RESOURCE_PATHS[table]
    return template.format(
        namespace=urllib.parse.quote(namespace(env), safe=""),
        compartment=urllib.parse.quote(compartment_id(env), safe=""),
    )


def resource_url(table: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Absolute URL for *table* on the configured endpoint.

    Composed from :func:`endpoint`, NEVER from an endpoint field in a response
    body -- see :data:`RESPONSE_ENDPOINTS_ARE_CONTAINER_LOCAL`.
    """
    return f"{endpoint(env)}{resource_path(table, env)}"


def rows_from(table: str, body: object) -> list:
    """Pull the row list out of a parsed response body for *table*.

    Exists because this emulator uses TWO envelopes: ``queues`` wraps its rows
    in ``{"items": [...]}`` and the other eight lanes return a bare list. A
    reader assuming either is wrong about the other.

    A shape that does not match yields ``[]``: this function answers "what rows
    are here", and it is the CALLER's job to have established that the request
    succeeded. An error body must never reach it.
    """
    key = RESPONSE_ROW_KEY.get(table, "items")
    if key is None:
        return body if isinstance(body, list) else []
    if not isinstance(body, dict):
        return []
    rows = body.get(key, [])
    return rows if isinstance(rows, list) else []


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
    return (src.get("FLOCI_OCI_DOCKER_SOCKET") or src.get("DOCKER_HOST") or "").strip()


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
    so Windows-with-no-``DOCKER_HOST`` is ``None``, never ``False``.

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

    NOT THE SAME QUESTION AS LISTING INVENTORY -- listing OKE clusters spawns
    nothing, so an inventory reader must not consult this.

    AND NOT A CLAIM THAT OKE WORKS. ``True`` here means "no proven reason to
    refuse". Measured, a create with a socket present still yields a cluster
    whose k3s container is dead (:data:`FABRICATED_ACTIVE_WITH_DOCKER`), so a
    caller must consult :data:`OKE_LIFECYCLE_IS_UNVERIFIED` before believing
    any resulting ``ACTIVE``.
    """
    return (
        service.strip().lower() not in CONTAINER_BACKED_SERVICES
        or docker_backed(env) is not False
    )


# ── Reachability / status ──────────────────────────────────────────────────


def _get_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(  # noqa: S310 -- operator-configured endpoint
        url, headers={"Accept": "application/json", "User-Agent": "ICDEV-EmulatorOci/1.0"}
    )


def reachable(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> bool:
    """Does the emulator answer its health endpoint? Costs one HTTP GET.

    An unrouted path on THIS emulator 404s cleanly -- unlike floci-az, where the
    routing filter answers 501 from the blob handler and "did not 404" proves
    nothing. So ``urlopen`` raising on a non-2xx is the correct probe here, and
    the seams may not borrow each other's.
    """
    url = f"{endpoint(env)}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(_get_request(url), timeout=timeout) as resp:  # noqa: S310
            resp.read()
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("floci-oci not reachable at %s: %s", url, exc)
        return False


def health(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> dict:
    """Parsed ``/health`` body, or ``{}`` if it could not be read.

    ``{}`` means UNREAD, never "healthy and empty" -- :func:`status` is what
    says which.
    """
    url = f"{endpoint(env)}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(_get_request(url), timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return body if isinstance(body, dict) else {}
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("floci-oci health unreadable at %s: %s", url, exc)
        return {}


def health_services(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> list[str]:
    """Service NAMES from ``/health``, sorted.

    NAMES ONLY, and the status values are dropped on purpose: they are all the
    literal ``"running"`` and are byte-identical on a deployment that cannot
    start a container (:data:`HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY`).
    Returning them would let a caller render a constant as a health badge.

    An empty list means the body was UNREAD or carried no map -- never evidence
    that services are down.
    """
    services = health(env, timeout=timeout).get("services")
    return sorted(services) if isinstance(services, dict) else []


def namespace_probed(
    env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0
) -> Optional[str]:
    """The Object Storage namespace the emulator REPORTS, or ``None``.

    ``GET /n/`` returns a bare JSON string. This is the one thing floci-oci
    offers that floci-gcp does not -- there, a project id was configuration and
    could not be discovered (``GET /v1/projects`` 404s).

    ``None`` means unread, never "no namespace". Callers wanting an answer that
    always works should use :func:`namespace`.
    """
    url = f"{endpoint(env)}/n/"
    try:
        with urllib.request.urlopen(_get_request(url), timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8", "replace"))
        return body if isinstance(body, str) and body else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("floci-oci namespace unreadable at %s: %s", url, exc)
        return None


def status(
    env: Optional[Mapping[str, str]] = None, *, probe: bool = True, timeout: float = 2.0
) -> str:
    """One of ``disabled | unreachable | degraded_no_docker | enabled``.

    Ordered by severity, and the order is the design:

      ``disabled``            the switch is off. Says nothing about the host.
      ``unreachable``         switched on, nothing answers at the endpoint.
      ``degraded_no_docker``  switched on and answering, but the socket is
                              PROVEN absent, so OKE cannot be created -- it
                              returns an honest 500. The ten REST inventory
                              lanes are unaffected.
      ``enabled``             switched on, answering, socket present or unproven.

    NOTE what ``enabled`` does NOT mean: OKE is broken in 0.4.0 whatever the
    socket says (:data:`FABRICATED_ACTIVE_WITH_DOCKER`). This vocabulary
    describes THIS DEPLOYMENT's ability to reach the emulator, never the
    emulator's own correctness.

    ``probe=False`` answers without touching the network.
    """
    if not enabled(env):
        return STATUS_DISABLED
    if probe and not reachable(env, timeout=timeout):
        return STATUS_UNREACHABLE
    if docker_backed(env) is False:
        return STATUS_DEGRADED_NO_DOCKER
    return STATUS_ENABLED


def unsupported_reason(operation: str = "write") -> str:
    """Why ICDEV will not perform *operation* against this emulator.

    One sentence NAMING the missing piece, because a capability gap and a
    failed call are different findings and a generic error sends someone to
    debug the emulator instead of the tree.
    """
    return (
        f"floci-oci {operation} is unsupported: ICDEV ships no OCI IaC executor "
        f"(the AWS analogue is tools/cloud/aws_config_executor.py) and its OCI "
        f"provider layer is stubbed -- see docs/spikes/flx-oci-parity.md §1."
    )
