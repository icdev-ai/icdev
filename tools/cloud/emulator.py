# CUI // SP-CTI
"""The ONE AWS-emulator switch for ICDEV (flx-seam-01).

THE DEFECT THIS REPLACES
------------------------
ICDEV had TWO unrelated switches for the same emulator and neither knew the
other existed:

  * ``tools/databridge/feature_flags.py::localstack()`` read
    ``LOCALSTACK_ENABLED`` (default false) and told the operator to run
    ``docker compose --profile localstack up -d`` -- a profile
    ``docker-compose.yml`` does not declare (its only profile is ``llm-proxy``).
  * ``tools/studio/executors/_base.py::detect_mode()`` returned
    ``localstack|sam|aws|dry_run`` keyed on the bare PRESENCE of
    ``LOCALSTACK_ENDPOINT`` -- and THAT is the switch under ``terraform apply``.

So the flag could be off while the executor ran against an emulator, or on
while nothing was reachable. Both now delegate here; do not write a third.

WHAT THIS MODULE IS
-------------------
A pure configuration seam. It reads environment; it does not start, stop or
configure anything, and NOTHING HERE TOUCHES THE NETWORK AT IMPORT TIME --
``reachable()``, ``health()`` and ``status(probe=True)`` are the only functions
that do, and only when asked. Every executor in ``tools/studio/executors``
imports this module, so an import-time probe would put a socket timeout on the
front of every workflow step.

The emulator of record is **floci** -- MIT, Java/Quarkus, port 4566, a
documented LocalStack drop-in that keeps ``/_localstack/health`` and translates
``LOCALSTACK_*`` env vars by default (measured read-only 2026-09-04).

NEVER source a performance, cost or capacity claim from emulator timings. An
emulator reproduces the AWS **API contract**, not its performance
characteristics -- the standing guard from
``docs/spikes/twx-spk-01-localstack-go-no-go.md``, which this project supersedes
on the air-gap question only (floci carries no auth-token image).

``degraded_no_docker`` IS LOAD-BEARING
--------------------------------------
A docker socket is needed ONLY for container-backed services (Lambda, RDS,
ElastiCache, OpenSearch, MSK, ECS/EC2/EKS). A caller answering for one of those
without a socket must report ``unsupported_without_docker``, NEVER an empty
list. Empty means "no functions"; unsupported means "this deployment cannot
answer". Conflating them is the ``rmf-disc-02`` ``nqe_client`` defect exactly --
every local NQE query raised on a table with no DDL anywhere in the repo, was
swallowed by a broad ``except``, returned ``[]``, and the attack-surface map
correlated every advisory against ZERO devices while reporting success.

Usage::

    from tools.cloud import emulator

    if emulator.enabled():
        client = boto3.client("s3", endpoint_url=emulator.endpoint(),
                              region_name=emulator.region())

    if not emulator.service_supported("lambda"):
        return {"status": emulator.UNSUPPORTED_WITHOUT_DOCKER}
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── The mode name detect_mode() returns ────────────────────────────────────
#
# `floci`, not `localstack`: the emulator is named, and a caller left comparing
# against the old string is a stale comparison a test can find.
MODE = "floci"

# ── The image ──────────────────────────────────────────────────────────────
#
# PINNED, and never ``:latest`` — an air-gapped rebuild has to be reproducible,
# and a moving tag makes "the image we tested" unanswerable. Pin by digest
# (``@sha256:...``) and record it in the SBOM before any real deployment.
#
# Read by tools/infra_canvas/dockerfile_generator.py, which emits this into
# CUSTOMER compose files (flx-gen-01). docker-compose.yml carries the same
# literal for ICDEV's own opt-in `floci` profile — YAML cannot import a Python
# constant, so those two are kept in step by hand; change both or neither.
DEFAULT_IMAGE_REPOSITORY = "floci/floci"
DEFAULT_IMAGE_TAG = "2.0.1"
DEFAULT_IMAGE = f"{DEFAULT_IMAGE_REPOSITORY}:{DEFAULT_IMAGE_TAG}"

#: Port the emulator serves the AWS API edge on.
DEFAULT_PORT = 4566

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_ENDPOINT = "http://localhost:4566"
DEFAULT_REGION = "us-gov-west-1"
DEFAULT_ACCOUNT_ID = "000000000000"
DEFAULT_ACCESS_KEY = "test"
DEFAULT_SECRET_KEY = "test"

# floci keeps LocalStack's health path -- that is what "drop-in" means here.
HEALTH_PATH = "/_localstack/health"

# ── The image, pinned, in ONE place ────────────────────────────────────────
#
# PINNED, never `:latest`, and never respelled. Every committed declaration of
# the emulator container -- the compose profile, the twelve canvas simulation
# topologies in tools/studio/sim, anything a generator emits -- reads `IMAGE`
# from here. A tag written out a second time is a second fact to keep in step,
# and the failure mode is silent: two declarations drift, one deployment runs
# 2.0.1 and another runs whatever `latest` resolved to that morning, and the
# only symptom is a behaviour difference nobody can attribute.
#
# `:latest` is refused for the air-gap reason as much as the reproducibility
# one -- an unpinned tag is a run-time pull, and a run-time pull cannot happen
# on the disconnected side this emulator was chosen to serve.
IMAGE_REPOSITORY = "floci/floci"
IMAGE_TAG = "2.0.1"
IMAGE = f"{IMAGE_REPOSITORY}:{IMAGE_TAG}"

#: The port floci listens on INSIDE its container. Always 4566 -- the host-side
#: port is a deployment's choice, this one is the emulator's.
CONTAINER_PORT = 4566

# floci's proxy ranges: host ports it forwards to CONTAINER-BACKED services.
#   6379-6399  ElastiCache / Redis
#   7001-7099  the external-service proxy range (Lambda, RDS, OpenSearch, ...)
#
# DECLARED HERE, DELIBERATELY NOT PUBLISHED BY EVERY CALLER. Reaching a service
# through one of these needs a docker socket mounted into the emulator, and a
# caller that publishes 119 host ports it cannot serve through has declared a
# capability nothing can consume -- while colliding with any second emulator on
# the same host, and with a local Redis on 6379. Publish them where a socket is
# actually mounted (the compose profile); everywhere else, honour them by
# keeping the API port out of the ranges.
PROXY_PORT_RANGES: tuple[tuple[int, int], ...] = ((6379, 6399), (7001, 7099))


def in_proxy_range(port: int) -> bool:
    """Is ``port`` inside one of floci's container-backed proxy ranges?"""
    return any(low <= port <= high for low, high in PROXY_PORT_RANGES)

# ── status() values ────────────────────────────────────────────────────────
STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_UNREACHABLE = "unreachable"
STATUS_DEGRADED_NO_DOCKER = "degraded_no_docker"

#: What a container-backed logical table must report when no socket is mounted.
#: NEVER an empty list -- see the module docstring.
UNSUPPORTED_WITHOUT_DOCKER = "unsupported_without_docker"

# AWS services floci backs with a CONTAINER, and so cannot serve without a
# docker socket. Everything else (s3, dynamodb, sqs, sns, ecr, iam, ssm, sts,
# kms, ...) is served in-process by the emulator and needs no socket.
CONTAINER_BACKED_SERVICES = frozenset(
    {
        "lambda",
        "rds",
        "rds-data",
        "elasticache",
        "opensearch",
        "es",
        "kafka",  # MSK
        "msk",
        "ecs",
        "ec2",
        "eks",
    }
)

# ── Deprecated aliases ─────────────────────────────────────────────────────
#
# Canonical FLOCI_* name -> deprecated LOCALSTACK_* alias, still READ.
#
# Kept rather than dropped for two reasons:
#   1. floci's own compat layer honours LOCALSTACK_* by default, so an operator
#      who set them has a working emulator and no reason to suspect ICDEV
#      stopped reading them.
#   2. tools/infra_canvas/dockerfile_generator.py has already emitted these
#      names into customer compose files we do not control.
DEPRECATED_ALIASES: dict[str, str] = {
    "FLOCI_ENABLED": "LOCALSTACK_ENABLED",
    "FLOCI_ENDPOINT": "LOCALSTACK_ENDPOINT",
    "FLOCI_REGION": "LOCALSTACK_REGION",
}

_ALIAS_WARNED: set[str] = set()


def reset_alias_warnings() -> None:
    """Forget which deprecated aliases have already been warned about.

    The warning is deduplicated per alias per process -- this seam is read on
    every call and a per-read warning would be noise. Tests that assert the
    warning fires call this first; nothing on a runtime path should.
    """
    _ALIAS_WARNED.clear()


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def _source(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _ALIAS_WARNED:
        return
    _ALIAS_WARNED.add(key)
    logger.warning(message, *args)


def _read(name: str, env: Optional[Mapping[str, str]] = None, *, default: str = "") -> str:
    """Read ``FLOCI_<X>``, falling back to its deprecated ``LOCALSTACK_<X>`` alias.

    A resolved alias logs ONE line, once per alias per process. An empty string
    is treated as unset -- an operator who wrote ``FLOCI_ENDPOINT=`` has not
    declared an endpoint, and letting it mask a set alias would make the
    deprecation silently lossy.
    """
    src = _source(env)
    val = src.get(name)
    if val:
        return val
    alias = DEPRECATED_ALIASES.get(name)
    if alias:
        aliased = src.get(alias)
        if aliased:
            _warn_once(
                alias,
                "%s is deprecated and will be removed; set %s instead "
                "(the alias is still honoured, deprecated 2026-09-04).",
                alias,
                name,
            )
            return aliased
    return default


# ── The switch ─────────────────────────────────────────────────────────────


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Is the AWS emulator switched ON for this deployment?

    ``FLOCI_ENABLED``, default **false**. The air-gap-safe posture is unchanged:
    an operator opts in explicitly, and a deployment that configures nothing
    reaches no emulator.

    This is the ONE answer. A caller deciding on "is some endpoint variable
    set" is deciding on configuration residue, not on an operator's intent.
    """
    return _truthy(_read("FLOCI_ENABLED", env, default="false"))


def endpoint(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator base URL, trailing slash stripped. Default ``http://localhost:4566``."""
    return _read("FLOCI_ENDPOINT", env, default=DEFAULT_ENDPOINT).rstrip("/")


def endpoint_declared(env: Optional[Mapping[str, str]] = None) -> bool:
    """Did the operator explicitly configure an emulator endpoint?

    Distinct from ``endpoint()``, which always answers (with the default). This
    is what tells a CONFIGURED emulator apart from an ASSUMED one, and callers
    use it to refuse the dangerous middle case: an endpoint declared while
    ``enabled()`` is false is a CONTRADICTION, and reading it as "no emulator,
    so use real AWS" is how a ``terraform apply`` meant for a local emulator
    reaches a real account.
    """
    src = _source(env)
    return bool(src.get("FLOCI_ENDPOINT") or src.get("LOCALSTACK_ENDPOINT"))


def region(env: Optional[Mapping[str, str]] = None) -> str:
    """Emulator region. Default ``us-gov-west-1`` -- ICDEV's target partition."""
    return _read("FLOCI_REGION", env, default=DEFAULT_REGION)


def account_id(env: Optional[Mapping[str, str]] = None) -> str:
    """12-digit AWS account id floci isolates its state per.

    A value that is not exactly 12 digits is a configuration error. It logs one
    line and falls back to the default rather than raising -- a getter that
    raises turns a typo into an unhandled exception inside whatever swallowing
    handler happens to surround it -- but note the consequence: two deployments
    that both typo their account id SHARE emulator state. Fix the value; do not
    rely on the fallback.
    """
    raw = _read("FLOCI_ACCOUNT_ID", env, default=DEFAULT_ACCOUNT_ID).strip()
    if len(raw) == 12 and raw.isdigit():
        return raw
    _warn_once(
        "FLOCI_ACCOUNT_ID:invalid",
        "FLOCI_ACCOUNT_ID=%r is not 12 digits; falling back to %s. "
        "Two deployments that both mis-set it will SHARE emulator state.",
        raw,
        DEFAULT_ACCOUNT_ID,
    )
    return DEFAULT_ACCOUNT_ID


def credentials(env: Optional[Mapping[str, str]] = None) -> tuple[str, str]:
    """``("test", "test")`` -- ALWAYS the dummy pair, never the ambient AWS one.

    The emulator accepts any non-empty pair, so a REAL ``AWS_ACCESS_KEY_ID``
    buys nothing here and costs something: these values are passed to
    `docker run -e` and into a Terraform provider block, so honouring the
    ambient pair would hand live credentials to a container that is talking to
    localhost. A developer with GovCloud keys exported in the same shell is the
    normal case, not the exotic one.

    ``env`` is accepted for signature symmetry with the rest of this module and
    is deliberately unread.
    """
    return (DEFAULT_ACCESS_KEY, DEFAULT_SECRET_KEY)


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
    return (src.get("FLOCI_DOCKER_SOCKET") or src.get("DOCKER_HOST") or "").strip()


def docker_basis(env: Optional[Mapping[str, str]] = None) -> str:
    """How ``docker_backed()`` reached its answer. See the ``BASIS_*`` constants."""
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

    TRI-STATE, and ``None`` (cannot tell) is the point. This is a cheap
    ENV-AND-FILESYSTEM question, deliberately not a ``docker info`` subprocess
    -- ``tools/studio/executors/_base.py::docker_available()`` is the expensive
    probe and stays the way to ask whether the daemon actually answers.

    MEASURED on this host 2026-09-04: Docker Desktop **28.5.1 was running** and
    ``os.path.exists(r"\\.\pipe\docker_engine")`` returned ``False``. A Windows
    named pipe is not reliably stat-able, so a plain existence check reports a
    definite absence for a working daemon. Returning ``False`` there would make
    ``service_supported("lambda")`` refuse a service that works -- a fabricated
    refusal, the same defect class as a fabricated ``[]`` pointing the other
    way. So Windows-with-no-``DOCKER_HOST`` is ``None``.

    ``None`` is NOT ``False``: callers must compare explicitly. The rule is
    "refuse only what is PROVEN unavailable" -- when we cannot tell, let the
    call go and let the emulator's own error be the evidence.
    """
    basis = docker_basis(env)
    if basis in (BASIS_DECLARED_REMOTE, BASIS_SOCKET_PRESENT):
        return True
    if basis == BASIS_SOCKET_ABSENT:
        return False
    return None


def service_supported(service: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Can this deployment answer for ``service``?

    ``False`` ONLY when the service is container-backed AND the socket is
    PROVEN absent. An unknown socket (``docker_backed() is None``) permits the
    call: the emulator's own error is better evidence than our guess.
    """
    if service.strip().lower() not in CONTAINER_BACKED_SERVICES:
        return True
    return docker_backed(env) is not False


# ── Reachability / status ──────────────────────────────────────────────────


def _health_request(env: Optional[Mapping[str, str]]) -> tuple[str, urllib.request.Request]:
    url = f"{endpoint(env)}{HEALTH_PATH}"
    return url, urllib.request.Request(  # noqa: S310 -- operator-configured endpoint
        url, headers={"Accept": "application/json", "User-Agent": "ICDEV-Emulator/1.0"}
    )


def reachable(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> bool:
    """Does the emulator answer its health endpoint? Costs one HTTP GET."""
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            resp.read()
        return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("emulator health probe failed for %s: %s", url, exc)
        return False


def health(env: Optional[Mapping[str, str]] = None, *, timeout: float = 2.0) -> dict:
    """Parsed ``/_localstack/health`` body, or ``{}`` when it could not be read.

    ``{}`` here means "not read", never "no services" -- read it beside
    ``status()``, which is what says which.
    """
    url, req = _health_request(env)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("emulator health read failed for %s: %s", url, exc)
        return {}


def status(
    env: Optional[Mapping[str, str]] = None, *, probe: bool = True, timeout: float = 2.0
) -> str:
    """One of ``disabled | unreachable | degraded_no_docker | enabled``.

    Ordered by severity, and the order is the design:

      ``disabled``            the switch is off. Says nothing about the host.
      ``unreachable``         switched on, nothing answers at the endpoint.
      ``degraded_no_docker``  switched on and answering, but the docker socket
                              is PROVEN absent, so container-backed services
                              (Lambda, RDS, ElastiCache, OpenSearch, MSK,
                              ECS/EC2/EKS) cannot be served. A caller answering
                              for one of those must say
                              ``unsupported_without_docker``, never ``[]``.
      ``enabled``             switched on, answering, and the socket is present
                              OR unproven.

    ``probe=False`` answers without touching the network -- for a caller that
    has already probed, and for an air-gapped test.
    """
    if not enabled(env):
        return STATUS_DISABLED
    if probe and not reachable(env, timeout=timeout):
        return STATUS_UNREACHABLE
    if docker_backed(env) is False:
        return STATUS_DEGRADED_NO_DOCKER
    return STATUS_ENABLED
