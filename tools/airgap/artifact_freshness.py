# CUI // SP-CTI
"""tools/airgap/artifact_freshness — is a PINNED artifact still the newest one? (xrv-pin-01)

THE DEFECT THIS EXISTS FOR
--------------------------
``vendor/images/images-floci.txt``, ``vendor/images/images-floci-runtime.txt``
and ``args/floci_runtime_images.yaml`` hold DIGEST pins with cryptographic
verification -- ``image_vendor.parse_pin`` refuses a tag outright, and
``verify_bundle`` re-hashes every blob in a bundle against the pin. That
machinery answers "does this tar contain exactly what we pinned" and it answers
it well. It has never answered, and cannot answer, "is what we pinned still the
current release".

Measured on this tree: the ``floci/floci:2.0.1`` pin is a 2026-09-01 snapshot
whose only ongoing assertion is
``tests/airgap/test_image_vendor.py::test_the_floci_pin_agrees_with_the_measured_digest``
-- a test that TWO FILES AGREE WITH EACH OTHER. Two files can agree perfectly
about a version that shipped a year ago. And the four ``floci/*`` compose tags,
the browser drivers and every external binary had no manifest at all, so there
was not even a list to ask the question about.

    AGREEMENT IS NOT CURRENCY. NOTHING ASKED UPSTREAM.

WHAT IT DOES, AND THE THREE STATES IT REFUSES TO MERGE
------------------------------------------------------
``args/pinned_artifacts.yaml`` enumerates the artifacts (it is a DECLARATION
and never a second copy of a pin -- each entry names the file the pin actually
lives in, and this module reads it). For each one:

    current        upstream's newest comparable release IS what we pin.
    behind         a newer one exists. NAMED, never a bare boolean.
    unmeasurable   we could not ask. An air-gapped host, a 4xx, an unparsable
                   answer, an exhausted budget. NEVER `current`.

``unmeasurable`` is the state the whole module turns on. An air-gapped
deployment -- the one this directory exists to serve -- reports every artifact
unmeasurable and reports it LOUDLY. A freshness checker that read "I could not
reach the registry" as "the pin is fine" would hand exactly the disconnected
operator a fabricated clean bill, which is the defect, not a degraded mode of
it. So ``current`` is only ever written by a request that returned an answer.

TWO BASES FOR `behind`, AND WHICH ONE DECIDED IS RECORDED
----------------------------------------------------------
``basis: version_tag``  the pinned tag carries an ORDERING (``2.0.1``,
                        ``16.3-alpine``) and a strictly greater tag of the same
                        SHAPE exists upstream. Shape matters: ``16.4-alpine``
                        is comparable to ``16.3-alpine`` and ``16.4`` is not --
                        a different suffix is a different image line, and
                        silently crossing them would report a postgres pin as
                        behind a variant nobody runs.
``basis: digest``       the pinned tag carries NO ordering. ``redpandadata/
                        redpanda:latest`` and ``rancher/k3s:latest`` are pinned
                        by a MUTABLE tag -- args/floci_runtime_images.yaml says
                        so in as many words -- so "newer" for them is knowable
                        only as the tag having MOVED off the digest we
                        recorded. That is a real, measurable `behind`, and it
                        is the one signal those two entries can give.
``basis: version``      a package index answered with a version string.

A TAG SOMEBODY ELSE CHOOSES IS NOT CHASING ITS OWN RELEASE TRAIN
-----------------------------------------------------------------
The shape rule above stops ``16.3-alpine`` being reported behind ``16.4``.
MEASURED 2026-09-12, it did not go far enough: the eleven floci RUNTIME base
images are refs the EMULATOR asks for, and it asks for them by a tag it builds
from its own defaults. Driven live, floci 2.0.1 logged ``Image already present
locally, skipping pull: postgres:16.3-alpine`` for a default
``CreateDBInstance(Engine=postgres)`` and ``Pulling image: postgres:16.99-alpine``
when ``EngineVersion=16.99`` was requested -- so the ref is
``postgres:<EngineVersion>-alpine`` and 16.3 is floci's DEFAULT, not our guess
at postgres's newest. Reporting that pin ``behind 18.6-alpine`` recommends
vendoring an image floci will never pull WHILE leaving the one it does pull out
of the air-gap bundle, which is the exact failure vendor/images exists to stop.
Six of the eleven were being reported that way, ``mysql:8.0.36 -> 26.7.0``
loudest among them.

So an entry may declare ``decided_by: consumer`` naming the ``consumer`` whose
currency really decides it (``floci``, itself an artifact in this manifest, so
the delegation is checkable and a test asserts it resolves). Such an entry is
decided on DIGEST DRIFT -- the one currency question upstream can answer about
a tag chosen by someone else -- and upstream's newest comparable tag is still
looked up and carried on ``upstream_newest``. It is reported, never hidden; it
simply does not decide the status. A consumer-decided pin moves when the
CONSUMER moves, which is a re-measurement (``python -m tools.cloud.runtime_images
--measure-help``), not a bump.

DIGEST DRIFT IS REPORTED ON EVERY IMAGE, not only the mutable ones. For a
semver-pinned image the STATUS is decided by the tag ordering and the drift
rides alongside as ``digest_drift`` -- ``True`` | ``False`` | ``None``, and
``None`` means we could not compare (no declared digest, or the HEAD did not
answer), never "no drift". A tag that has silently moved under a digest pin is
a different finding from a stale pin and sends a reader somewhere else.

IT NEVER PULLS, AND NEVER WRITES A PIN
---------------------------------------
Structural, not a docstring promise:

  * ``subprocess`` is never imported -- so ``docker pull`` cannot be spelled
    here at all, and ``image_vendor.ALLOWED_DOCKER_COMMANDS`` (which has never
    contained ``pull``) is not weakened by a second module standing beside it;
  * every HTTP call goes through ``_http``, which is GET/HEAD only and refuses
    any other method;
  * nothing opens ``vendor/images/*``, ``docker-compose.yml``,
    ``requirements.txt`` or ``args/pinned_artifacts.yaml`` for WRITING.

``tests/airgap/test_artifact_freshness.py`` reads this module's AST to prove
all three. A behavioural test over today's callers would still pass the day an
edit threads a "just bump it" flag through, which is the one change that turns
a read-only survey into an unaudited actuator over the supply chain.

WHAT IT DOES NOT DO
-------------------
It does not decide whether to MOVE a pin. ``behind`` files a kanban card (the
``artifact_freshness`` genesis reflex) and a human decides -- moving a digest
pin is a supply-chain act and it belongs in a reviewed diff, which is exactly
why the repository pins digests in the first place.

Usage::

    python -m tools.airgap.artifact_freshness --survey --json
    python -m tools.airgap.artifact_freshness --survey
    python -m tools.airgap.artifact_freshness --artifact floci --json
    python -m tools.airgap.artifact_freshness --list

Exit 0 = a survey was produced, whatever it says. Exit 2 = it could not be,
which is never the same as a clean survey.
"""
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory -- never
# the import root. Bootstrap it before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

logger = get_logger("icdev.airgap.artifact_freshness")

BASE_DIR = repo_root(__file__)
CONFIG_PATH = BASE_DIR / "args" / "pinned_artifacts.yaml"
COMPOSE_PATH = BASE_DIR / "docker-compose.yml"
REQUIREMENTS_PATH = BASE_DIR / "requirements.txt"

# ── The three states, kept apart on purpose. See the module docstring. ──────
STATUS_CURRENT = "current"
STATUS_BEHIND = "behind"
STATUS_UNMEASURABLE = "unmeasurable"
STATUSES = (STATUS_CURRENT, STATUS_BEHIND, STATUS_UNMEASURABLE)

BASIS_VERSION_TAG = "version_tag"
BASIS_DIGEST = "digest"
BASIS_VERSION = "version"

#: ``decided_by: consumer`` -- the pinned TAG is chosen by a named consumer in
#: this manifest, not by the artifact's own release train. See the module
#: docstring and args/pinned_artifacts.yaml. The only value there is: an
#: unrecognised ``decided_by`` is ``unmeasurable``, never silently ignored.
DECIDED_BY_CONSUMER = "consumer"

KIND_IMAGE = "image"
KIND_PACKAGE = "package"

#: An `upstream:` label is cross-checked against the registry host the `ref`
#: implies, so a declaration can never disagree with the fetch it describes.
UPSTREAM_HOSTS = {
    "dockerhub": "registry-1.docker.io",
    "ecr_public": "public.ecr.aws",
    "ghcr": "ghcr.io",
}
DEFAULT_REGISTRY = "registry-1.docker.io"

#: Only GET and HEAD. There is no method here that could change anything
#: upstream, and an AST test asserts the frozenset is what `_http` consults.
ALLOWED_HTTP_METHODS = frozenset({"GET", "HEAD"})

_MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)

#: `<numeric dotted core><everything else>`. The tail is the SHAPE: only tags
#: sharing it are comparable (see the docstring on `basis: version_tag`).
_VERSION_SHAPE = re.compile(r"^(?P<core>\d+(?:\.\d+)*)(?P<suffix>.*)$")
_CHALLENGE_KV = re.compile(r'(\w+)="([^"]*)"')
_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)\s*(?P<op>==|>=|~=)?\s*(?P<ver>[^\s;#]*)")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read args/pinned_artifacts.yaml. Raises on an unreadable declaration.

    An unreadable manifest is exit 2, never an empty survey: "no artifact is
    behind" and "I could not read the list of artifacts" are different answers
    and only one of them is reassuring.
    """
    import yaml

    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        raise FileNotFoundError(f"pinned-artifact manifest not found: {target}")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{target} did not parse to a mapping")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError(f"{target} declares no artifacts")
    return data


# ---------------------------------------------------------------------------
# Pin resolution — read the pin where it LIVES, never restate it here
# ---------------------------------------------------------------------------


def resolve_pin(entry: Dict[str, Any]) -> Dict[str, Any]:
    """What does this entry's ``pin_source`` actually declare?

    Returns ``{digest, tag, pin_strength, errors[]}``. Every value may be
    ``None``: a pin source that carries no digest (a compose tag) yields
    ``digest: None``, which makes drift UNMEASURABLE rather than absent.
    """
    out: Dict[str, Any] = {
        "digest": None,
        "tag": None,
        "pin_strength": None,
        "errors": [],
    }
    source = str(entry.get("pin_source") or "")
    ref = str(entry.get("ref") or "")

    digest_source = entry.get("digest_source") or (
        source if source.startswith("vendor/images/") else None
    )
    if digest_source:
        try:
            out["digest"] = _digest_from_pin_file(Path(str(digest_source)), ref)
            out["pin_strength"] = "digest"
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"digest_source {digest_source}: {exc}")

    if source.startswith("compose:"):
        try:
            tag = _tag_from_compose(source.split(":", 1)[1], ref)
            out["tag"] = tag
            out["pin_strength"] = out["pin_strength"] or "tag"
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"{source}: {exc}")
    elif source == "requirements.txt":
        try:
            version, op = _version_from_requirements(ref)
            out["tag"] = version
            out["pin_strength"] = "floor" if op in (">=", "~=") else "pinned"
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"requirements.txt: {exc}")

    return out


def _digest_from_pin_file(relative: Path, ref: str) -> Optional[str]:
    """The digest ``vendor/images/images-<topic>.txt`` records for ``ref``.

    Parsed through ``image_vendor.parse_pin`` -- the ONE statement of what a
    pin line is. A second parser here is how the vendor and this survey would
    come to disagree about a file neither of them changed.
    """
    from tools.airgap.image_vendor import parse_pin

    path = BASE_DIR / relative
    if not path.exists():
        raise FileNotFoundError(f"no such pin file: {relative}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pin = parse_pin(line)
        if pin["repo"] == ref:
            return pin["digest"]
    return None


def _tag_from_compose(service: str, ref: str) -> str:
    """The tag ``docker-compose.yml`` gives ``service``, cross-checked on repo."""
    import yaml

    if not COMPOSE_PATH.exists():
        raise FileNotFoundError("docker-compose.yml not found")
    doc = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")) or {}
    services = doc.get("services") or {}
    spec = services.get(service)
    if not isinstance(spec, dict):
        raise KeyError(f"no compose service {service!r}")
    image = str(spec.get("image") or "")
    if not image:
        raise KeyError(f"compose service {service!r} declares no image")
    repo, _, tag = image.rpartition(":")
    if not repo or "/" in tag:
        raise ValueError(f"compose image {image!r} carries no tag")
    if repo != ref:
        raise ValueError(f"compose service {service!r} names {repo!r}, manifest says {ref!r}")
    return tag


def _version_from_requirements(distribution: str) -> Tuple[str, str]:
    """``(version, operator)`` for ``distribution`` in requirements.txt."""
    if not REQUIREMENTS_PATH.exists():
        raise FileNotFoundError("requirements.txt not found")
    wanted = distribution.lower().replace("_", "-")
    for raw in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQ_LINE.match(line)
        if not match:
            continue
        if match.group("name").lower().replace("_", "-") != wanted:
            continue
        return match.group("ver") or "", match.group("op") or "=="
    raise KeyError(f"{distribution!r} is not declared in requirements.txt")


# ---------------------------------------------------------------------------
# HTTP — one door, GET/HEAD only, bounded
# ---------------------------------------------------------------------------


def _http(
    url: str,
    *,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """One read-only request. ``{status, headers, body}``; never raises for 4xx.

    The method allowlist is the point: it is what makes "this survey cannot
    change anything upstream" a property of the code rather than a claim.
    """
    if method not in ALLOWED_HTTP_METHODS:
        raise RuntimeError(
            f"refusing HTTP {method}: artifact_freshness may only "
            f"{sorted(ALLOWED_HTTP_METHODS)} -- it observes, it never acts."
        )
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read() if method == "GET" else b""
            return {
                "status": int(response.status),
                "headers": {k.lower(): v for k, v in response.headers.items()},
                "body": body.decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": int(exc.code),
            "headers": {k.lower(): v for k, v in (exc.headers or {}).items()},
            "body": "",
        }
    except Exception as exc:  # noqa: BLE001 -- URLError, timeout, DNS, TLS
        return {"status": None, "headers": {}, "body": "", "error": str(exc)[:200]}


#: The seam tests script. Nothing else in this module opens a socket.
_HTTP = _http


def _json_body(response: Dict[str, Any]) -> Optional[Any]:
    try:
        return json.loads(response.get("body") or "")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Registry v2 — host derived from the ref, token from the 401 challenge
# ---------------------------------------------------------------------------


def split_ref(ref: str) -> Tuple[str, str]:
    """``ref`` -> ``(registry_host, repository)``.

    ``postgres`` -> ``(registry-1.docker.io, library/postgres)``;
    ``valkey/valkey`` -> ``(registry-1.docker.io, valkey/valkey)``;
    ``public.ecr.aws/lambda/python`` -> ``(public.ecr.aws, lambda/python)``.
    """
    head, slash, tail = ref.partition("/")
    if slash and ("." in head or ":" in head or head == "localhost"):
        return head, tail
    if not slash:
        return DEFAULT_REGISTRY, f"library/{ref}"
    return DEFAULT_REGISTRY, ref


def _bearer_for(challenge: str, timeout: float) -> Optional[str]:
    """Fetch a pull token from the realm the registry's 401 advertised.

    Derived from the challenge rather than a per-registry table, so Docker Hub,
    public.ecr.aws and ghcr.io are one code path and a fourth registry needs no
    edit here.
    """
    if not challenge.lower().startswith("bearer"):
        return None
    fields = dict(_CHALLENGE_KV.findall(challenge))
    realm = fields.pop("realm", None)
    if not realm:
        return None
    query = urllib.parse.urlencode({k: v for k, v in fields.items() if v})
    url = f"{realm}?{query}" if query else realm
    response = _HTTP(url, timeout=timeout)
    payload = _json_body(response) or {}
    if not isinstance(payload, dict):
        return None
    token = payload.get("token") or payload.get("access_token")
    return str(token) if token else None


def _registry_get(
    host: str, path: str, *, method: str, accept: Optional[str], timeout: float
) -> Dict[str, Any]:
    """One registry request, retried ONCE with a token if challenged."""
    url = f"https://{host}/v2/{path}"
    headers = {"Accept": accept} if accept else {}
    response = _HTTP(url, method=method, headers=headers, timeout=timeout)
    if response.get("status") == 401:
        token = _bearer_for(response["headers"].get("www-authenticate", ""), timeout)
        if token:
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {token}"
            response = _HTTP(url, method=method, headers=headers, timeout=timeout)
    return response


def observed_digest(ref: str, tag: str, *, timeout: float) -> Dict[str, Any]:
    """The digest the registry serves behind ``ref:tag`` right now, or a reason."""
    host, repo = split_ref(ref)
    response = _registry_get(
        host, f"{repo}/manifests/{tag}", method="HEAD", accept=_MANIFEST_ACCEPT, timeout=timeout
    )
    status = response.get("status")
    if status != 200:
        return {
            "digest": None,
            "reason": f"manifest HEAD {ref}:{tag} -> "
            + (response.get("error") or f"HTTP {status}"),
        }
    digest = response["headers"].get("docker-content-digest")
    if not digest:
        return {"digest": None, "reason": "registry returned no Docker-Content-Digest header"}
    return {"digest": digest.strip(), "reason": None}


def list_tags(ref: str, *, timeout: float) -> Dict[str, Any]:
    """Every tag the registry lists for ``ref``, or a reason it could not say."""
    host, repo = split_ref(ref)
    response = _registry_get(host, f"{repo}/tags/list", method="GET", accept=None, timeout=timeout)
    status = response.get("status")
    if status != 200:
        return {
            "tags": None,
            "reason": f"tags/list {ref} -> " + (response.get("error") or f"HTTP {status}"),
        }
    payload = _json_body(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("tags"), list):
        return {"tags": None, "reason": "tags/list returned an unparsable body"}
    return {"tags": [str(t) for t in payload["tags"]], "reason": None}


# ---------------------------------------------------------------------------
# Ordering — same SHAPE only
# ---------------------------------------------------------------------------


def version_shape(tag: str) -> Optional[Tuple[Tuple[int, ...], str]]:
    """``2.0.1`` -> ``((2,0,1), '')``; ``16.3-alpine`` -> ``((16,3), '-alpine')``.

    ``None`` for a tag with no numeric core (``latest``, ``stable``). Such a tag
    carries no ordering, and inventing one for it is how ``:latest`` would be
    reported ``current`` forever.
    """
    match = _VERSION_SHAPE.match(tag.strip())
    if not match:
        return None
    core = tuple(int(part) for part in match.group("core").split("."))
    return core, match.group("suffix")


def newest_comparable(pinned: str, tags: List[str]) -> Optional[str]:
    """The greatest tag of the SAME shape as ``pinned``, if it is greater.

    Comparison is on the numeric core only and only within one suffix, so
    ``16.4-alpine`` can supersede ``16.3-alpine`` and ``16.4`` cannot -- a
    different suffix is a different image line, and crossing them would report
    a pin as behind a variant nobody runs.
    """
    shape = version_shape(pinned)
    if shape is None:
        return None
    pinned_core, suffix = shape
    best_core, best_tag = pinned_core, None
    for tag in tags:
        candidate = version_shape(tag)
        if candidate is None or candidate[1] != suffix:
            continue
        if len(candidate[0]) != len(pinned_core):
            # `16.4.1` is not a comparable successor to `16.3` -- the pin names
            # a two-component line. Reported by NOT being picked, never by a
            # guess about which line the deployment meant.
            continue
        if candidate[0] > best_core:
            best_core, best_tag = candidate[0], tag
    return best_tag


# ---------------------------------------------------------------------------
# Per-artifact verdict
# ---------------------------------------------------------------------------


def _unmeasurable(entry: Dict[str, Any], reason: str, **extra: Any) -> Dict[str, Any]:
    result = {
        "name": entry.get("name"),
        "kind": entry.get("kind"),
        "ref": entry.get("ref"),
        "pinned": entry.get("pinned"),
        "upstream": entry.get("upstream"),
        "pin_source": entry.get("pin_source"),
        "status": STATUS_UNMEASURABLE,
        "basis": None,
        "newest": None,
        "reason": reason,
        "declared_digest": None,
        "observed_digest": None,
        "digest_drift": None,
        "pin_strength": None,
        # Set only on the consumer lane, and always PRESENT so a reader never
        # has to tell "not delegated" from "key absent".
        "decided_by": None,
        "consumer": None,
        "upstream_newest": None,
    }
    result.update(extra)
    return result


def _decide_by_digest(
    result: Dict[str, Any],
    *,
    pinned: str,
    pin: Dict[str, Any],
    seen: Dict[str, Any],
    what: str,
) -> Dict[str, Any]:
    """`behind` iff the tag no longer serves the digest we recorded.

    The lane for every pin whose TAG ordering cannot decide the question --
    ``:latest``, which has no ordering at all, and a consumer-chosen tag, whose
    ordering is real but is not ours to chase. ``what`` names which, because
    the two send a reader somewhere different.
    """
    if seen["digest"] is None:
        result["reason"] = seen["reason"]
        return result
    if pin["digest"] is None:
        result["reason"] = f"{what} with no declared digest -- nothing to compare it against"
        return result
    result["basis"] = BASIS_DIGEST
    if result["digest_drift"]:
        result["status"] = STATUS_BEHIND
        result["newest"] = seen["digest"]
        result["reason"] = (
            f"the {what} {pinned!r} has moved: pinned {pin['digest']}, "
            f"upstream now serves {seen['digest']}"
        )
    else:
        result["status"] = STATUS_CURRENT
        result["reason"] = None
    return result


def _decide_by_consumer(
    result: Dict[str, Any],
    *,
    ref: str,
    pinned: str,
    consumer: str,
    pin: Dict[str, Any],
    seen: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    """A tag a CONSUMER chooses is not chasing its own upstream release train.

    Upstream's newest comparable tag is still looked up and carried on
    ``upstream_newest`` -- not chasing a release is not the same as not knowing
    about it, and a survey that dropped it would be hiding the thing it exists
    to find. It just does not DECIDE the status, because a release the consumer
    will never request is not a release this tree can move to. What decides it
    is the one currency question upstream can answer about somebody else's
    choice: is that tag still serving the bytes we recorded?
    """
    result["decided_by"] = DECIDED_BY_CONSUMER
    result["consumer"] = consumer

    listing = list_tags(ref, timeout=timeout)
    if listing["tags"] is not None:
        result["tags_seen"] = len(listing["tags"])
        result["upstream_newest"] = newest_comparable(pinned, listing["tags"])

    decided = _decide_by_digest(
        result, pinned=pinned, pin=pin, seen=seen,
        what=f"tag {consumer} chooses",
    )
    if decided["status"] == STATUS_CURRENT:
        newer = decided["upstream_newest"]
        decided["reason"] = (
            f"{consumer} requests {ref}:{pinned} and the tag still serves the pinned digest"
            + (
                f"; upstream also publishes {newer!r}, which {consumer} does not request"
                if newer else ""
            )
        )
    return decided


def check_artifact(entry: Dict[str, Any], *, timeout: float = 10.0) -> Dict[str, Any]:
    """One artifact's three-state verdict. Never raises; never fabricates."""
    name = str(entry.get("name") or "")
    kind = str(entry.get("kind") or "")
    ref = str(entry.get("ref") or "")
    pinned = str(entry.get("pinned") or "")
    upstream = str(entry.get("upstream") or "")

    if not (name and kind and ref and pinned):
        return _unmeasurable(entry, "incomplete declaration")

    pin = resolve_pin(entry)
    if pin["errors"]:
        # A manifest that disagrees with the file holding the pin is NOT
        # resolved in favour of either side: which one is right is the question,
        # and answering it by preference is how the drift becomes invisible.
        return _unmeasurable(
            entry, "pin_source_unreadable: " + "; ".join(pin["errors"]),
            pin_strength=pin["pin_strength"],
        )
    if pin["tag"] is not None and pin["tag"] != pinned:
        return _unmeasurable(
            entry,
            f"pin_source_disagrees: {entry.get('pin_source')} says {pin['tag']!r}, "
            f"the manifest says {pinned!r}",
            pin_strength=pin["pin_strength"],
        )

    if kind == KIND_PACKAGE:
        return _check_package(entry, pin, timeout=timeout)
    if kind != KIND_IMAGE:
        return _unmeasurable(entry, f"unsupported kind {kind!r}", pin_strength=pin["pin_strength"])

    host, _repo = split_ref(ref)
    expected = UPSTREAM_HOSTS.get(upstream)
    if expected is None:
        return _unmeasurable(
            entry, f"unsupported upstream {upstream!r} for an image", pin_strength=pin["pin_strength"]
        )
    if expected != host:
        return _unmeasurable(
            entry,
            f"declared_upstream_mismatch: upstream={upstream} implies {expected}, "
            f"ref={ref} implies {host}",
            pin_strength=pin["pin_strength"],
        )

    result = _unmeasurable(entry, "not measured")
    result["pin_strength"] = pin["pin_strength"]
    result["declared_digest"] = pin["digest"]

    seen = observed_digest(ref, pinned, timeout=timeout)
    result["observed_digest"] = seen["digest"]
    if pin["digest"] and seen["digest"]:
        result["digest_drift"] = pin["digest"] != seen["digest"]

    decided_by = str(entry.get("decided_by") or "").strip()
    if decided_by:
        if decided_by != DECIDED_BY_CONSUMER:
            # An unreadable declaration must never resolve to a clean bill.
            result["reason"] = f"unsupported decided_by {decided_by!r}"
            return result
        consumer = str(entry.get("consumer") or "").strip()
        if not consumer:
            result["reason"] = "decided_by: consumer names no `consumer`"
            return result
        return _decide_by_consumer(
            result, ref=ref, pinned=pinned, consumer=consumer,
            pin=pin, seen=seen, timeout=timeout,
        )

    orderable = version_shape(pinned) is not None
    if not orderable:
        # `:latest`. The ONLY knowable `behind` is the tag having moved off the
        # digest we recorded -- args/floci_runtime_images.yaml's own caveat.
        return _decide_by_digest(
            result, pinned=pinned, pin=pin, seen=seen, what="mutable tag"
        )

    listing = list_tags(ref, timeout=timeout)
    if listing["tags"] is None:
        result["reason"] = listing["reason"]
        return result
    result["basis"] = BASIS_VERSION_TAG
    result["tags_seen"] = len(listing["tags"])
    newest = newest_comparable(pinned, listing["tags"])
    if newest:
        result["status"] = STATUS_BEHIND
        result["newest"] = newest
        result["reason"] = f"upstream publishes {newest!r}; this tree pins {pinned!r}"
    else:
        result["status"] = STATUS_CURRENT
        result["reason"] = None
    return result


def _check_package(entry: Dict[str, Any], pin: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    """A package index lane. Reuses the EXISTING PyPI seam, never a second one."""
    result = _unmeasurable(entry, "not measured")
    result["pin_strength"] = pin["pin_strength"]
    upstream = str(entry.get("upstream") or "")
    if upstream != "pypi":
        result["reason"] = f"unsupported upstream {upstream!r} for a package"
        return result

    try:
        from tools.maintenance.dependency_scanner import _check_pypi_latest
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"pypi seam unavailable: {exc}"
        return result

    try:
        latest, released = _check_pypi_latest(str(entry.get("ref")))
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"pypi lookup failed: {str(exc)[:160]}"
        return result
    if not latest:
        result["reason"] = "pypi returned no version"
        return result

    pinned = str(entry.get("pinned") or "")
    result["basis"] = BASIS_VERSION
    result["released"] = released
    here, there = version_shape(pinned), version_shape(latest)
    if here is None or there is None:
        result["reason"] = f"cannot order {pinned!r} against {latest!r}"
        return result
    if there[0] > here[0]:
        result["status"] = STATUS_BEHIND
        result["newest"] = latest
        result["reason"] = f"PyPI publishes {latest!r}; this tree declares {pinned!r}"
    else:
        result["status"] = STATUS_CURRENT
        result["reason"] = None
    return result


# ---------------------------------------------------------------------------
# Survey
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """The ONE place a percentage is computed here.

    ``None`` -- never 0.0 and never 100.0 -- over an empty denominator.
    ``pct if total else 100.0`` at this line would breach
    args/perfect_score_gate.yaml, which rem-hyg-13 ratcheted to zero sites.
    """
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 1)


def survey(
    *,
    config: Optional[Dict[str, Any]] = None,
    only: Optional[str] = None,
    offline: Optional[bool] = None,
) -> Dict[str, Any]:
    """Every declared artifact, each with a three-state status.

    ``offline`` forces the air-gap verdict; ``None`` asks
    ``tools.airgap.detector.is_airgap``.
    """
    started = time.time()
    config = config if config is not None else load_config()
    timeout = float(config.get("timeout_seconds") or 10.0)
    budget = float(config.get("budget_seconds") or 180.0)
    entries: List[Dict[str, Any]] = [
        dict(e) for e in config.get("artifacts") or [] if isinstance(e, dict)
    ]
    if only:
        entries = [e for e in entries if str(e.get("name")) == only]

    if offline is None:
        try:
            from tools.airgap.detector import is_airgap

            offline = bool(is_airgap())
        except Exception:  # noqa: BLE001
            offline = False

    results: List[Dict[str, Any]] = []
    for entry in entries:
        if offline:
            # The disconnected side reports that it could not ask. It never
            # reports that the pins are fine -- that is the fabricated clean
            # bill this module exists to refuse.
            results.append(_unmeasurable(entry, "airgap: no upstream is reachable from here"))
            continue
        if time.time() - started > budget:
            results.append(
                _unmeasurable(entry, f"budget_exhausted after {budget:.0f}s -- not asked")
            )
            continue
        try:
            results.append(check_artifact(entry, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 -- one bad entry never kills a survey
            logger.warning("artifact_freshness: %s raised: %s", entry.get("name"), exc)
            results.append(_unmeasurable(entry, f"probe raised: {str(exc)[:160]}"))

    counts = {status: sum(1 for r in results if r["status"] == status) for status in STATUSES}
    measured = counts[STATUS_CURRENT] + counts[STATUS_BEHIND]
    drifted = [r["name"] for r in results if r.get("digest_drift") is True]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": len(results),
        "counts": counts,
        "measured": measured,
        # None, never 100.0, over an empty denominator: a survey that asked
        # nothing must not render a full green bar.
        "current_pct": _rate(counts[STATUS_CURRENT], measured),
        "offline": bool(offline),
        "behind": [r["name"] for r in results if r["status"] == STATUS_BEHIND],
        "unmeasurable": [r["name"] for r in results if r["status"] == STATUS_UNMEASURABLE],
        "digest_drift": drifted,
        "results": results,
        "elapsed_seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(report: Dict[str, Any]) -> str:
    lines = [
        "Pinned-artifact freshness (xrv-pin-01)",
        f"  generated {report['generated_at']}   offline={report['offline']}",
        f"  {report['artifacts']} declared | current {report['counts']['current']} "
        f"| behind {report['counts']['behind']} "
        f"| unmeasurable {report['counts']['unmeasurable']}",
    ]
    pct = report["current_pct"]
    lines.append(
        f"  current of measured: {pct}%" if pct is not None
        else "  current of measured: not measured (nothing was asked)"
    )
    lines.append("")
    for row in report["results"]:
        mark = {"current": "ok  ", "behind": "BEHIND", "unmeasurable": "?   "}[row["status"]]
        lines.append(f"  {mark} {row['name']:<24} {row['ref']}:{row['pinned']}")
        if row.get("newest"):
            lines.append(f"         newest: {row['newest']}   basis={row['basis']}")
        if row.get("digest_drift") is True:
            lines.append("         digest drift: the pinned tag no longer serves the pinned digest")
        newer = row.get("upstream_newest")
        if newer and newer not in (row.get("reason") or ""):
            # Named even when it did not decide the status -- but once, not twice.
            lines.append(
                f"         upstream also publishes {newer} -- "
                f"{row['consumer']} does not request it"
            )
        if row.get("reason"):
            lines.append(f"         {row['reason']}")
    if report["unmeasurable"]:
        lines += [
            "",
            "  UNMEASURABLE IS NOT CLEAN. The artifacts above with `?` were not "
            "measured at all;",
            "  nothing here says their pins are current.",
        ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.airgap.artifact_freshness",
        description="Is a pinned artifact still the newest one upstream? (xrv-pin-01)",
    )
    parser.add_argument("--survey", action="store_true", help="probe every declared artifact")
    parser.add_argument("--artifact", help="probe ONE artifact by name")
    parser.add_argument("--list", action="store_true", help="list the declared artifacts")
    parser.add_argument("--offline", action="store_true", help="force the air-gap verdict")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        print(f"artifact_freshness: cannot read the manifest: {exc}", file=sys.stderr)
        return 2

    if args.list:
        rows = [
            {k: e.get(k) for k in ("name", "kind", "ref", "pinned", "pin_source", "upstream")}
            for e in config["artifacts"]
        ]
        print(json.dumps(rows, indent=2) if args.json
              else "\n".join(f"{r['name']:<24} {r['kind']:<8} {r['ref']}:{r['pinned']}" for r in rows))
        return 0

    if not (args.survey or args.artifact):
        parser.error("one of --survey, --artifact or --list is required")

    try:
        report = survey(
            config=config,
            only=args.artifact,
            offline=True if args.offline else None,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"artifact_freshness: survey could not be produced: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, default=str) if args.json else _render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
