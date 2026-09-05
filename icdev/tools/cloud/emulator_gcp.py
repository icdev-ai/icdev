# CUI // SP-CTI
"""The ONE GCP-emulator switch for ICDEV (flx-gcp-01).

A SIBLING OF ``emulator.py`` AND ``emulator_az.py``, NOT A SECOND COPY
----------------------------------------------------------------------
``emulator.py`` is the AWS seam (floci, 4566), ``emulator_az.py`` the Azure one
(floci-az, 4577). This is the GCP seam (floci-gcp, 4588). They are separate
modules rather than one parameterised module because **almost nothing about
them is shared**, and every difference below was MEASURED against
``floci/floci-gcp:0.8.0`` on 2026-09-05 -- see ``docs/spikes/flx-gcp-parity.md``:

  * a third health path. floci keeps ``/_localstack/health``, floci-az answers
    ``/_floci/health``, and this one answers **``/health``** and returns 404 on
    both of the others. Three emulators, three paths, no shared contract;
  * a health body that DOES carry a services map -- where floci-az has none --
    and the map is not a health signal (see below);
  * a different environment prefix (``FLOCI_GCP_*``);
  * a different tenancy noun: a PROJECT, not a subscription and not an account
    id;
  * and, the reason this card exists, **a completely different client
    contract**. boto3 takes an ``endpoint_url``; GCP client libraries do not,
    and read standard ``*_EMULATOR_HOST`` variables instead. So this seam
    EXPORTS ENVIRONMENT (:func:`emulator_host_env`) where its siblings hand
    back a URL.

WHAT THIS MODULE IS
-------------------
A pure configuration seam. It reads environment; it does not start, stop or
configure anything, and NOTHING HERE TOUCHES THE NETWORK AT IMPORT TIME --
:func:`reachable`, :func:`health` and ``status(probe=True)`` are the only
functions that do, and only when asked. :func:`emulator_host_env` RETURNS a
mapping; it never mutates ``os.environ``, so a caller decides the scope.

READ AND INVENTORY ONLY -- THERE IS NO IaC EXECUTION HERE
---------------------------------------------------------
ICDEV ships ``tools/cloud/aws_config_executor.py`` and **no GCP analogue**.
:data:`IAC_EXECUTION_SUPPORTED` is ``False`` and is asserted by a test.
Declaring execution support that no executor backs is the declared-but-
unconsumed defect this platform ships most, and it is refused here explicitly.

THE MEASURED TRAPS THIS SEAM EXISTS TO STATE ONCE
-------------------------------------------------
1. :data:`HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY`. ``/health`` publishes 23
   services, every one reading ``"running"``. Measured on two containers -- one
   with the host socket mounted, one with ``FLOCI_GCP_DOCKER_DOCKER_HOST``
   pointed at a provably absent path -- **the two bodies are byte-identical**,
   and the socket-absent deployment cannot start a container at all. The map is
   a registry listing wearing a measurement's name. It is MORE dangerous than
   floci-az's absent map, because a consumer will believe it.

2. :data:`GRPC_ONLY_SERVICES`. Firestore and Datastore answer **no REST path**
   -- every one tried returned 404 or 405 -- while the same operations over
   gRPC on the same port answered immediately. ICDEV's connector stack reads
   over HTTP, so it cannot read them at all, and a REST 404 looks exactly like
   "no such resource". Neither is a connector table.

3. :data:`PATH_COLLISIONS`. Google's documented GKE path
   ``/v1/projects/{p}/locations/{l}/clusters`` is served **by the Managed Kafka
   handler**. Proven, not inferred: a create against it spawned
   ``floci-gcp-kafka-…`` running Redpanda, and the list returns a body carrying
   ``bootstrapAddress``. GKE itself works, at ``/container/v1/…``
   (:data:`GKE_PATH_PREFIX`) -- so a GKE client pointed at the documented path
   gets Kafka clusters and no error.

4. :data:`FABRICATED_SUCCESS_WITHOUT_DOCKER`. Cloud SQL and Kafka return 500
   without a docker socket. **Cloud Run returns 200** and a service body
   indistinguishable from a real deployment. That is why docker-backing is
   decided HERE, from :func:`docker_backed`, and never from a response.

WHAT THIS EMULATOR DOES *NOT* HAVE, and the sibling seam must not lend it
-------------------------------------------------------------------------
The floci-az subscription-scope trap has **no GCP analogue** -- project-scoped
lists reflect writes, measured for buckets, topics, secrets and key rings. And
there are no host-forwarded proxy port ranges: spawned services are addressed
by Docker BRIDGE IP (measured: ``172.17.0.3:5432`` for a Cloud SQL instance,
``5432/tcp`` exposed and unpublished), so this module deliberately carries no
``PROXY_PORT_RANGES`` constant.

Usage::

    from tools.cloud import emulator_gcp

    if emulator_gcp.enabled():
        env = {**os.environ, **emulator_gcp.emulator_host_env()}
        subprocess.run([...], env=env)     # a GCP client in that child talks
                                           # to the emulator, with no code change
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping, NamedTuple, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── The mode name a caller compares against ────────────────────────────────
MODE = "floci-gcp"

# ── The image ──────────────────────────────────────────────────────────────
#
# PINNED, never ``:latest`` -- an air-gapped rebuild has to be reproducible, and
# a moving tag makes "the image we tested" unanswerable. docker-compose.yml
# carries the same literal for the opt-in `floci-gcp` profile; YAML cannot
# import a Python constant, so those two are kept in step by hand and a test
# pins them equal. Change both or neither.
IMAGE_REPOSITORY = "floci/floci-gcp"
IMAGE_TAG = "0.8.0"
IMAGE = f"{IMAGE_REPOSITORY}:{IMAGE_TAG}"

#: Digest MEASURED from the pulled image on 2026-09-05. Recorded so an air-gap
#: bundle can be verified by digest rather than by tag -- a tag-only check reads
#: a `docker load`ed bundle as absent (see the flx-airgap-01 discipline).
IMAGE_DIGEST = "sha256:5037d304aded5ab4ccf4697239131521fe66b8952f411f6c1781c9166d2ab01b"

#: Port floci-gcp serves on, INSIDE the container. The host-side port is a
#: deployment's choice; this one is the emulator's. MEASURED: it is the ONLY
#: listening socket in the container -- the gRPC server is multiplexed onto it
#: rather than taking a second port (see :data:`GRPC_SHARES_THE_HTTP_PORT`).
CONTAINER_PORT = 4588
DEFAULT_PORT = 4588

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_ENDPOINT = "http://localhost:4588"

#: A GCP region name. NOT ``us-gov-west-1`` (AWS's spelling) and not
#: ``usgovvirginia`` (Azure's) -- a third vocabulary, which is half the reason
#: these seams are not one parameterised module.
DEFAULT_REGION = "us-central1"

#: The project floci-gcp seeds. MEASURED from ``GET /v1/projects/floci-local``,
#: which returns a real project body with a projectNumber.
DEFAULT_PROJECT_ID = "floci-local"

#: floci-gcp's OWN health path. ``/_floci/health`` (the Azure sibling's) and
#: ``/_localstack/health`` (the AWS one's) both return **404** here.
HEALTH_PATH = "/health"

#: MEASURED: the health body DOES carry a ``services`` map, unlike floci-az.
#: Read :data:`HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY` before believing it.
HEALTH_HAS_SERVICE_MAP = True

#: THE TRAP. The map is byte-identical on a deployment that provably cannot
#: start a container, and ``"running"`` is the only value ever observed. It
#: reports what is ENABLED, never what is WORKING. Kept as its own constant
#: rather than folded into the one above because the two say different things
#: and a caller needs both: the map parses AND it is not evidence.
HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY = True

#: MEASURED: ``/health`` reports ``"version":"0.8.0"``, matching the image's own
#: ``FLOCI_GCP_VERSION``. The one constant that inverts in the helpful
#: direction relative to floci-az (which reports ``"dev"``) -- recorded for
#: symmetry, and load-bearing for nothing.
HEALTH_REPORTS_REAL_VERSION = True

#: ICDEV has no GCP IaC executor. See the module docstring.
IAC_EXECUTION_SUPPORTED = False

#: MEASURED: ``GET /v1/projects`` returns 404 while ``GET /v1/projects/{id}``
#: returns a real project. A project id cannot be DISCOVERED from this
#: emulator; it is configuration. So :func:`project_id` always answers, and no
#: caller may try to enumerate.
PROJECT_LIST_IS_UNSUPPORTED = True

#: MEASURED: only ``4588/tcp`` listens inside the container, and a gRPC channel
#: to it served eight real methods while two control methods returned
#: UNIMPLEMENTED. One port, two transports -- which is why every variable in
#: :data:`EMULATOR_HOST_VARS` names the same host:port despite differing in
#: form.
GRPC_SHARES_THE_HTTP_PORT = True

#: MEASURED: spawned service containers are reachable on the Docker BRIDGE
#: network (``172.17.0.3:5432`` for Cloud SQL), with the port EXPOSED and NOT
#: published to the host. There are therefore no host proxy port ranges to
#: declare -- and a process on the host cannot dial a spawned service.
SPAWNED_SERVICES_ARE_BRIDGE_ADDRESSED = True

# ── status() values ────────────────────────────────────────────────────────
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_UNREACHABLE = "unreachable"
STATUS_DEGRADED_NO_DOCKER = "degraded_no_docker"

#: What a container-backed logical table must report when no socket is mounted.
#: NEVER an empty list -- empty means "no resources", unsupported means "this
#: deployment cannot answer".
UNSUPPORTED_WITHOUT_DOCKER = "unsupported_without_docker"

# ── Service classification, all MEASURED ───────────────────────────────────

#: Services that spawn a container, measured by WHAT THEY SPAWNED rather than
#: from any self-report: cloudsql -> postgres:15.18-alpine, kafka ->
#: redpandadata/redpanda:latest, gke -> rancher/k3s:latest, cloudrun -> the
#: user's own image (nginx:alpine in the probe).
CONTAINER_BACKED_SERVICES = frozenset({"cloudsql", "kafka", "gke", "cloudrun"})

#: THE DANGEROUS SUBSET. Without a docker socket, cloudsql and kafka return a
#: 500 carrying a dockerjava stack trace -- ugly, but unmistakable. **Cloud Run
#: returns 200**, and a subsequent GET returns a service body with uid,
#: generation, createTime, traffic and a urls entry, structurally identical to a
#: deployment that really has a container behind it.
#:
#: This set is the argument for deciding docker-backing in
#: :func:`docker_backed` rather than from a response. It is not consulted by
#: :func:`data_plane_supported` -- that function already refuses the whole
#: container-backed set -- it exists so a caller writing a NEW code path can
#: see which services will lie to it.
FABRICATED_SUCCESS_WITHOUT_DOCKER = frozenset({"cloudrun"})

#: Services with NO REST lane. Every REST path tried returned 404 or 405; the
#: same operations over gRPC answered. ICDEV's connector reads over HTTP, so
#: these are unreadable by it -- and a REST 404 is indistinguishable from "no
#: such resource", which is why this is stated rather than left to be
#: rediscovered. NOT merged with :data:`DECLARED_UNREACHABLE_SERVICES`: these
#: work, over a transport we do not speak, which is a different repair.
GRPC_ONLY_SERVICES = frozenset({"firestore", "datastore"})

#: Services the emulator lists as enabled that NO route reached on 2026-09-05.
#: The claim is bounded -- *no route found*, not *no route exists*: the spike
#: records that an earlier draft wrongly put firebaseauth, sts and gke here,
#: and all three work once probed with the right verb, media type or prefix.
#: Nothing in this tree may declare a capability over one of these.
DECLARED_UNREACHABLE_SERVICES = frozenset({"cloudtasks"})

#: Google's DOCUMENTED path -> what actually serves it here. A caller composing
#: the left-hand form gets the right-hand service's data with a 200 and no
#: error. Only one collision was measured; the mapping exists so a second one
#: has an obvious home.
PATH_COLLISIONS: dict[str, str] = {
    "/v1/projects/{project}/locations/{location}/clusters": "kafka",
}

#: GKE's real prefix on this emulator. ``/container/v1``, not ``/v1`` -- see
#: :data:`PATH_COLLISIONS`. Measured: a create here returned a genuine
#: ``operationType: CREATE_CLUSTER`` and spawned rancher/k3s.
GKE_PATH_PREFIX = "/container/v1"

# ── REST lanes this seam will compose ──────────────────────────────────────
#
# Every entry ANSWERED (HTTP 200) on 2026-09-05 over REST, and the writes made
# in the same session were reflected by the corresponding list -- so a 200 here
# means the lane reflects state rather than merely existing.
#
# ``{project}`` and ``{location}`` are substituted by :func:`resource_path`.
#
# Deliberately ABSENT and each for a stated reason: firestore/datastore (gRPC
# only), cloudtasks (no route found), the bare ``/v1/.../clusters`` form (it is
# Kafka's -- gke_clusters below uses the /container/v1 prefix).
REST_RESOURCE_PATHS: dict[str, str] = {
    "project": "/v1/projects/{project}",
    "buckets": "/storage/v1/b?project={project}",
    "topics": "/v1/projects/{project}/topics",
    "secrets": "/v1/projects/{project}/secrets",
    "key_rings": "/v1/projects/{project}/locations/{location}/keyRings",
    "service_accounts": "/v1/projects/{project}/serviceAccounts",
    "sql_instances": "/sql/v1beta4/projects/{project}/instances",
    "datasets": "/bigquery/v2/projects/{project}/datasets",
    "gke_clusters": GKE_PATH_PREFIX + "/projects/{project}/locations/{location}/clusters",
}

#: Which service each REST table belongs to -- used to decide whether a table is
#: refusable when no socket is mounted. Only ``sql_instances`` and
#: ``gke_clusters`` name container-backed services; LISTING them is not the same
#: question as reaching their data plane, which is why the connector consults
#: :func:`data_plane_supported` rather than this mapping alone.
TABLE_SERVICE: dict[str, str] = {
    "project": "resourcemanager",
    "buckets": "gcs",
    "topics": "pubsub",
    "secrets": "secretmanager",
    "key_rings": "kms",
    "service_accounts": "iam",
    "sql_instances": "cloudsql",
    "datasets": "bigquery",
    "gke_clusters": "gke",
}

#: The key each lane returns its rows under. MEASURED, and it is NOT uniform --
#: nine lanes answer a keyed empty and six answer a bare ``{}`` with no key at
#: all, so ``body["items"]`` raises on half of them. ``None`` means the body IS
#: the resource (``project`` returns one object, not a list).
RESPONSE_ROW_KEY: dict[str, Optional[str]] = {
    "project": None,
    "buckets": "items",
    "topics": "topics",
    "secrets": "secrets",
    "key_rings": "keyRings",
    "service_accounts": "accounts",
    "sql_instances": "items",
    "datasets": "datasets",
    "gke_clusters": "clusters",
}


# ── THE ENV CONTRACT -- what this card is about ────────────────────────────


class EmulatorHostVar(NamedTuple):
    """One ``*_EMULATOR_HOST`` variable, and how we know what shape it takes.

    ``form`` is ``host_port`` or ``url``, and the split is the whole point:
    getting it wrong fails at the CLIENT, and this emulator will never tell us
    -- ``grep -a EMULATOR_HOST`` over its native binary returns NOTHING, so it
    neither reads nor validates any of these names.

    ``basis`` records provenance honestly:

      ``measured_transport``  the TRANSPORT was exercised on this host, and the
                              form follows from it. gRPC was addressed without a
                              scheme (a channel to ``localhost:4588`` served
                              eight real methods); the REST base was addressed
                              WITH one, and the emulator said so itself -- its
                              ``selfLink`` fields come back as
                              ``http://localhost:4588/storage/v1/b/...``.
      ``declared``            the variable's client-side reading was NOT
                              exercised. No ``google-cloud-*`` library is
                              installed here and installing one to check would
                              add an undeclared dependency to the environment
                              the tsg-iso-03 census governs.

    Every entry is ``declared`` for the client-side half. The distinction that
    matters is whether the LANE behind it answered, which is what ``service``
    plus :data:`GRPC_ONLY_SERVICES` / :data:`DECLARED_UNREACHABLE_SERVICES`
    let a reader work out.
    """

    name: str
    service: str
    transport: str  # "grpc" | "rest"
    form: str  # "host_port" | "url"
    basis: str


FORM_HOST_PORT = "host_port"
FORM_URL = "url"

#: THE EXPORTED SET. The card named six variables "all pointing at
#: localhost:4588"; that is right about the TARGET -- one port serves both
#: transports -- and wrong about the FORM. Five take a bare ``host:port`` and
#: ``STORAGE_EMULATOR_HOST`` takes a URL WITH SCHEME.
#:
#: Ordered as the card lists them, so the two can be read side by side.
EMULATOR_HOST_VARS: tuple[EmulatorHostVar, ...] = (
    EmulatorHostVar("PUBSUB_EMULATOR_HOST", "pubsub", "grpc", FORM_HOST_PORT, "declared"),
    EmulatorHostVar(
        "FIRESTORE_EMULATOR_HOST", "firestore", "grpc", FORM_HOST_PORT, "declared"
    ),
    EmulatorHostVar(
        "DATASTORE_EMULATOR_HOST", "datastore", "grpc", FORM_HOST_PORT, "declared"
    ),
    EmulatorHostVar("STORAGE_EMULATOR_HOST", "gcs", "rest", FORM_URL, "declared"),
    EmulatorHostVar(
        "SECRET_MANAGER_EMULATOR_HOST",
        "secretmanager",
        "grpc",
        FORM_HOST_PORT,
        "declared",
    ),
    EmulatorHostVar(
        "FIREBASE_AUTH_EMULATOR_HOST", "firebaseauth", "rest", FORM_HOST_PORT, "declared"
    ),
)

#: The variable names, in declaration order. What a test pins.
EMULATOR_HOST_VAR_NAMES: tuple[str, ...] = tuple(v.name for v in EMULATOR_HOST_VARS)


def host_port(env: Optional[Mapping[str, str]] = None) -> str:
    """``host:port`` for the configured endpoint -- no scheme, no trailing slash.

    Derived from :func:`endpoint` rather than from
    :data:`DEFAULT_PORT`, so an operator who moved the emulator gets a
    consistent answer on both sides of :func:`emulator_host_env`.

    A URL with no parseable netloc falls back to the default endpoint's,
    warning once: a getter that raises here would take an operator's typo and
    surface it as an unhandled exception inside whatever swallowing handler
    surrounds the call.
    """
    parsed = urllib.parse.urlsplit(endpoint(env))
    if parsed.netloc:
        return parsed.netloc
    _warn_once(
        "FLOCI_GCP_ENDPOINT:unparsed",
        "FLOCI_GCP_ENDPOINT=%r has no host:port; falling back to %s.",
        endpoint(env),
        DEFAULT_ENDPOINT,
    )
    return urllib.parse.urlsplit(DEFAULT_ENDPOINT).netloc


def emulator_host_env(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The environment a GCP client needs to reach this emulator.

    **THE SEAM'S REASON TO EXIST.** boto3 takes an ``endpoint_url``; GCP client
    libraries take no such parameter and read these variables instead. So where
    the AWS and Azure seams hand back a URL for a caller to pass, this one hands
    back environment for a caller to *export*.

    RETURNS a mapping and NEVER mutates ``os.environ``. Which processes see
    these names is the caller's decision, and a seam that reached into the
    ambient environment would make it for every thread in the process.

    Returns ``{}`` when the emulator is switched off -- exporting these names
    while nothing is listening points every GCP client in the child process at
    a dead port, which is strictly worse than leaving them unset.

    The FORM is per-variable and measured; see :data:`EMULATOR_HOST_VARS`.
    """
    if not enabled(env):
        return {}
    hp = host_port(env)
    url = endpoint(env)
    return {v.name: (url if v.form == FORM_URL else hp) for v in EMULATOR_HOST_VARS}


def emulator_host_env_forced(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """:func:`emulator_host_env` without the enabled() short-circuit.

    For a caller that has ALREADY decided to talk to the emulator -- a test
    harness, or a compose-driven integration run -- and does not want the answer
    to depend on a switch it is not consulting. Named so the bypass is visible
    at the call site; do not reach for it to "fix" an empty mapping.
    """
    hp = host_port(env)
    url = endpoint(env)
    return {v.name: (url if v.form == FORM_URL else hp) for v in EMULATOR_HOST_VARS}


# ── env reading ────────────────────────────────────────────────────────────


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def _source(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _read(name: str, env: Optional[Mapping[str, str]] = None, *, default: str = "") -> str:
    """Read ``FLOCI_GCP_<X>``.

    NO ALIAS LAYER, deliberately -- for the same reason as the Azure seam. The
    AWS seam honours ``LOCALSTACK_*`` because floci translates those names
    itself and ICDEV had already emitted them into compose files it does not
    control. Neither is true here, and inventing an alias would create the
    deprecation debt rather than absorb it.

    Note what this is NOT: the ``*_EMULATOR_HOST`` names in
    :data:`EMULATOR_HOST_VARS` are an OUTPUT of this seam, never an input. The
    seam does not read them, so an operator who set ``PUBSUB_EMULATOR_HOST`` by
    hand cannot silently redirect ICDEV's own reads.

    An empty string is treated as unset -- an operator who wrote
    ``FLOCI_GCP_ENDPOINT=`` has not declared an endpoint.
    """
    val = _source(env).get(name)
    return val if val else default


# ── The switch ─────────────────────────────────────────────────────────────


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is the GCP emulator switched ON for this deployment?

    ``FLOCI_GCP_ENABLED``, default **false**. Air-gap-safe: an operator opts in
    explicitly, and a deployment that configures nothing reaches no emulator.

    Deliberately INDEPENDENT of ``FLOCI_ENABLED`` and ``FLOCI_AZ_ENABLED``. A
    deployment running one emulator has said nothing about wanting a third, and
    coupling them would start a container nobody asked for.
    """
    return _truthy(_read("FLOCI_GCP_ENABLED", env, default="false"))


def endpoint(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator base URL, trailing slash stripped. Default ``http://localhost:4588``."""
    return _read("FLOCI_GCP_ENDPOINT", env, default=DEFAULT_ENDPOINT).rstrip("/")


def endpoint_declared(env: Optional[Mapping[str, str]] = None) -> bool:
    """Did the operator explicitly configure an emulator endpoint?

    Distinct from :func:`endpoint`, which always answers (with the default).
    This is what tells a CONFIGURED emulator apart from an ASSUMED one.
    """
    return bool(_source(env).get("FLOCI_GCP_ENDPOINT"))


def region(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator region/location. Default ``us-central1``."""
    return _read("FLOCI_GCP_REGION", env, default=DEFAULT_REGION)


def project_id(env: Optional[Mapping[str, str]] = None) -> str:
    """Project every REST path below is scoped to.

    ALWAYS ANSWERS, and it has to: ``GET /v1/projects`` returns 404 on this
    emulator (:data:`PROJECT_LIST_IS_UNSUPPORTED`), so a project id cannot be
    discovered and a caller with none has nothing to fall back on.

    A malformed value logs one line and falls back rather than raising -- the
    same reasoning as :func:`host_port`. Note the consequence: two deployments
    that both mis-set it share emulator state. Fix the value.
    """
    raw = _read("FLOCI_GCP_PROJECT_ID", env, default=DEFAULT_PROJECT_ID).strip()
    if _looks_like_project_id(raw):
        return raw
    _warn_once(
        "FLOCI_GCP_PROJECT_ID:invalid",
        "FLOCI_GCP_PROJECT_ID=%r is not a valid project id; falling back to %s.",
        raw,
        DEFAULT_PROJECT_ID,
    )
    return DEFAULT_PROJECT_ID


def _looks_like_project_id(value: str) -> bool:
    """Google's project-id rule: 6-30 chars, lowercase letter first, then
    lowercase letters, digits or hyphens, and not ending in a hyphen.

    Enforced because a project id is INTERPOLATED INTO A URL PATH. A value
    carrying ``/`` or ``..`` would compose a path pointing somewhere else
    entirely, and this is the only validation between an env var and that URL.
    """
    if not 6 <= len(value) <= 30:
        return False
    if value[0] not in "abcdefghijklmnopqrstuvwxyz" or value.endswith("-"):
        return False
    return all(c.islower() or c.isdigit() or c == "-" for c in value)


_WARNED: set[str] = set()


def reset_warnings() -> None:
    """Forget which one-shot warnings have fired. For tests; not a runtime path."""
    _WARNED.clear()


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(message, *args)


# ── REST path construction ─────────────────────────────────────────────────


def resource_path(table: str, env: Optional[Mapping[str, str]] = None) -> str:
    """The REST path for *table*, project and location substituted.

    The ONE place a path is composed, so no caller hand-builds the
    ``/v1/.../clusters`` form that :data:`PATH_COLLISIONS` records as Kafka's.

    :raises KeyError: if *table* is not a measured-answering REST lane. That is
        deliberate for firestore/datastore -- they answer gRPC only, and
        returning a plausible REST path for them would hand a caller a URL that
        404s and reads as "no such resource".
    """
    template = REST_RESOURCE_PATHS[table]
    return template.format(project=project_id(env), location=region(env))


def resource_url(table: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Absolute URL for *table* on the configured endpoint."""
    return f"{endpoint(env)}{resource_path(table, env)}"


def rows_from(table: str, body: object) -> list:
    """Pull the row list out of a parsed response body for *table*.

    Exists because the row key is NOT uniform across lanes
    (:data:`RESPONSE_ROW_KEY`) and several lanes answer a bare ``{}`` with no
    key at all -- ``body["items"]`` raises on half of them. A body that is not a
    dict, or a key that is absent, yields ``[]``: this function answers "what
    rows are here", and it is the CALLER's job to have established that the
    request succeeded. An error body must never reach it.
    """
    if not isinstance(body, dict):
        return []
    key = RESPONSE_ROW_KEY.get(table, "items")
    if key is None:  # the body IS the resource
        return [body] if body else []
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
    return (src.get("FLOCI_GCP_DOCKER_SOCKET") or src.get("DOCKER_HOST") or "").strip()


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

    NOT THE SAME QUESTION AS LISTING INVENTORY. Listing Cloud SQL instances or
    GKE clusters spawns nothing, so an inventory reader must not consult this;
    only a caller about to CREATE or connect should. And note the asymmetry the
    measurement found: without a socket, cloudsql and kafka fail loudly with a
    500, while cloudrun returns a fabricated 200
    (:data:`FABRICATED_SUCCESS_WITHOUT_DOCKER`) -- so for one of these four,
    this function is the ONLY thing standing between a caller and a success
    that did not happen.
    """
    return (
        service.strip().lower() not in CONTAINER_BACKED_SERVICES
        or docker_backed(env) is not False
    )


# ── Reachability / status ──────────────────────────────────────────────────


def _health_request(env: Optional[Mapping[str, str]]) -> tuple[str, urllib.request.Request]:
    url = f"{endpoint(env)}{HEALTH_PATH}"
    return url, urllib.request.Request(  # noqa: S310 -- operator-configured endpoint
        url, headers={"Accept": "application/json", "User-Agent": "ICDEV-EmulatorGcp/1.0"}
    )


def reachable(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> bool:
    """Does the emulator answer its health endpoint? Costs one HTTP GET.

    Unlike floci-az -- where an unrouted path falls into the blob handler and
    answers 501 -- an unrouted path here 404s, in one of two shapes. So
    ``urlopen`` raising on a non-2xx is the right behaviour on both emulators,
    for different reasons, and neither seam may borrow the other's probe.
    """
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            resp.read()
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("gcp emulator health probe failed for %s: %s", url, exc)
        return False


def health(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> dict:
    """Parsed ``/health`` body, or ``{}`` when it could not be read.

    ``{}`` here means "not read", never "no services" -- read it beside
    :func:`status`, which is what says which.

    MEASURED shape: ``{"services": {<23 names>: "running"}, "version": "0.8.0"}``.
    The version IS the real release. The services map IS NOT A HEALTH SIGNAL --
    see :data:`HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY` and :func:`health_services`.
    """
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("gcp emulator health read failed for %s: %s", url, exc)
        return {}


def health_services(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> list[str]:
    """Service names the emulator declares ENABLED. **Not what is working.**

    Named ``health_services`` because that is where the data lives, and
    documented this hard because the name invites exactly the wrong reading.
    Measured 2026-09-05: the map is byte-identical on a deployment that
    provably cannot start a container, and ``"running"`` is the only value it
    was ever observed to hold.

    Returns NAMES only, deliberately -- handing back the ``{name: "running"}``
    mapping would put a status string in a caller's hands that means nothing,
    and someone would render it.

    An empty list means the body could not be read OR declared nothing; it is
    never evidence that services are down.
    """
    services = health(env, timeout=timeout).get("services")
    return sorted(services) if isinstance(services, dict) else []


def status(
    env: Optional[Mapping[str, str]] = None, *, probe: bool = True, timeout: float = 2.0
) -> str:
    """One of ``disabled | unreachable | degraded_no_docker | enabled``.

    Ordered by severity, and the order is the design:

      ``disabled``            the switch is off. Says nothing about the host.
      ``unreachable``         switched on, nothing answers at the endpoint.
      ``degraded_no_docker``  switched on and answering, but the socket is
                              PROVEN absent, so the container-backed services
                              (Cloud SQL, Kafka, GKE, Cloud Run) cannot be
                              created -- and Cloud Run will claim otherwise.
                              The REST inventory lanes are unaffected.
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
