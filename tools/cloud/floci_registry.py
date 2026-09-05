# CUI // SP-CTI
"""Where would floci's run-time pull actually GO? (flx-airgap-03)

THE QUESTION IS UNCHANGED, AND THAT IS THE DESIGN
--------------------------------------------------
flx-airgap-02 asked whether the LOCAL CACHE already holds the eleven measured
base images. That is the right discriminator for a site that can pre-seed each
host. A registry-mandating site cannot: its images have to be SERVED, by an
internal mirror, to whatever daemon floci talks to.

So the discriminator generalises. The one question
:mod:`tools.cloud.runtime_images` has always asked --

    would this deployment need an EXTERNAL pull at run time?

-- now has two ways to answer "no": the image is already cached, or the pull it
would make goes to an INTERNAL mirror. This module supplies the second half.
There is deliberately NO second rule and no second evaluator: a rule that
answered "is it cached" and another that answered "is it mirrored" could
disagree about what a run-time pull is, and then a reviewer has two verdicts
and no way to choose.

INTERNAL MEANS WHAT THE AIR-GAP RULES ALREADY SAY IT MEANS
-----------------------------------------------------------
:func:`is_internal_host` reads ``allowlist.internal_host_suffixes`` from
``args/twin_airgap_rules.yaml`` -- the SAME list ``airgap-internal-registry``
matches registry hosts against. A private copy here could call a host internal
that the neighbouring rule calls public, and both would be "the rule".

A DECLARED MIRROR IS A FACT ABOUT THE CONFIGURATION, NOT ABOUT THE MIRROR
--------------------------------------------------------------------------
Nothing here contacts a registry. ``ALLOWED_DOCKER_COMMANDS`` in
``tools/airgap/image_vendor.py`` contains no ``pull`` and no ``manifest``, and
this module does not widen it or open a second subprocess door. So what is
established is that the pull is INTERNAL, which is the air-gap question.
Whether the mirror actually holds the image is MIRROR COMPLETENESS -- a
different question with a different repair (load the vendored bundle into the
mirror), and folding it in silently would turn an operations gap into an
air-gap violation or, far worse, the other way round. :func:`pull_origin`
reports ``verified: False`` on every mirrored row so no caller can mistake one
for the other.

``daemon_registry_mirror`` CANNOT REDIRECT public.ecr.aws, AND SAYING IT CAN
IS A GREEN VERDICT FOR A HOST THAT STILL REACHES THE INTERNET
-----------------------------------------------------------------------------
Docker's ``registry-mirrors`` daemon setting is documented as applying to
**Docker Hub pulls only**. Nine of the eleven measured images come from Docker
Hub and are redirected by it; the two Lambda runtimes come from
``public.ecr.aws`` and are not. A declaration naming ``daemon_registry_mirror``
for any registry other than ``docker.io`` is therefore REFUSED at load time
rather than believed -- the alternative is a deployment that reads ``satisfied``
and pulls from Amazon on first Lambda invoke. Re-hosting those images in the
mirror and pointing floci at them is ``repository_rewrite``.

A CREDENTIAL IS A REFERENCE, NEVER A LITERAL
---------------------------------------------
``CREDENTIAL_REF_PREFIXES`` matches ``seed_connections.SECRET_REF_PREFIXES``
exactly and a test pins them equal. A value that is not one of those prefixes
is REFUSED, not warned about -- a warning still lands the secret in git, and
this repository is public. ``plain:`` is not accepted even though
``tools/rag/secret_ref.py`` resolves it: that prefix exists to carry a literal.
Nothing here ever RESOLVES a reference either. This module answers a question
about air-gap posture; it has no use for the secret's value, and a resolver
here would put a live credential in the memory of a linter.

Usage::

    from tools.cloud import floci_registry

    origin = floci_registry.pull_origin("postgres:16.3-alpine")
    if origin["external"]:
        ...  # this pull leaves the enclave

CLI::

    python -m tools.cloud.floci_registry --show --json
    python -m tools.cloud.floci_registry --check
    python -m tools.cloud.floci_registry --origins --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cloud.floci_registry")

BASE_DIR = repo_root(__file__)
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "floci_registry.yaml"
AIRGAP_RULES_PATH = BASE_DIR / "args" / "twin_airgap_rules.yaml"

#: The prefixes a credential reference may start with. Pinned equal to
#: ``tools.databridge.seed_connections.SECRET_REF_PREFIXES`` by a test rather
#: than imported: importing that module here would drag the databridge schema
#: layer into every air-gap evaluation for the sake of one tuple.
CREDENTIAL_REF_PREFIXES: tuple[str, ...] = ("env:", "vault:", "aws:", "file:")

#: How a mirror actually intercepts the pull. See the module docstring.
MECHANISM_DAEMON_MIRROR = "daemon_registry_mirror"
MECHANISM_REPOSITORY_REWRITE = "repository_rewrite"
MECHANISMS: tuple[str, ...] = (MECHANISM_DAEMON_MIRROR, MECHANISM_REPOSITORY_REWRITE)

#: Docker Hub is the implicit registry for a ref naming no host, and it is the
#: ONLY registry ``registry-mirrors`` redirects.
DOCKER_HUB = "docker.io"

# -- Per-image pull origins. Never merged: each sends you to a different fix. --
ORIGIN_INTERNAL_MIRROR = "internal_mirror"
ORIGIN_NO_MIRROR = "no_mirror_declared"
ORIGIN_MIRROR_NOT_INTERNAL = "mirror_not_internal"
ORIGIN_MIRROR_DISABLED = "registry_declaration_disabled"

#: The env var naming the daemon FLOCI talks to. It is NOT
#: ``FLOCI_DOCKER_SOCKET`` (that one is read by emulator.docker_basis() to
#: answer how the HOST PYTHON PROCESS reaches a daemon) and it is NOT
#: ``FLOCI_DOCKER_SOCKET_MOUNT`` (the compose bind-mount source). Keeping the
#: three apart is the trap docker-compose.yml documents at length.
DOCKER_HOST_ENV = "FLOCI_DOCKER_DOCKER_HOST"

#: What the floci container gets when the variable is unset: the socket compose
#: mounts into it. Kept in step with docker-compose.yml's floci service.
DEFAULT_CONTAINER_DOCKER_HOST = "unix:///var/run/docker.sock"

_CONFIG_CACHE: dict | None = None
_SUFFIX_CACHE: list[str] | None = None


class RegistryDeclarationError(ValueError):
    """The declaration is unusable. Raised, never logged-and-continued."""


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_declaration(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load, validate and cache the registry declaration.

    A MISSING file is the shipped default posture, not an error: it means no
    mirror is declared, which is exactly what flx-airgap-02 assumed. A file that
    EXISTS and is malformed is a different thing and raises -- silently
    degrading a broken mirror declaration to "no mirror" would hand a
    registry-mandating site the wrong verdict without saying so.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and path is None and not force:
        return _CONFIG_CACHE
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        cfg = _read_yaml(cfg_path)
    except FileNotFoundError:
        logger.info("no floci registry declaration at %s - no mirror declared", cfg_path)
        cfg = {"enabled": False, "registries": [], "_reason": f"no declaration at {cfg_path}"}
    validate(cfg)
    if path is None:
        _CONFIG_CACHE = cfg
    return cfg


def _require_ref(entry_id: str, key: str, value: Any) -> str | None:
    ref = str(value or "").strip()
    if not ref:
        return None
    if not ref.startswith(CREDENTIAL_REF_PREFIXES):
        raise RegistryDeclarationError(
            f"registry {entry_id!r}: {key} is a LITERAL, not a reference. It must start "
            f"with one of {list(CREDENTIAL_REF_PREFIXES)}. A credential value must never "
            f"appear in a YAML file in this repository - put it in the configured secret "
            f"backend and reference it here. (`plain:` is deliberately not accepted: that "
            f"prefix exists to carry a literal.)"
        )
    return ref


def validate(cfg: Mapping[str, Any]) -> dict:
    """Refuse an unusable declaration. Returns the normalised registry map."""
    entries = cfg.get("registries") or []
    if not isinstance(entries, list):
        raise RegistryDeclarationError("`registries` must be a list")

    normalised: dict[str, dict] = {}
    for raw in entries:
        if not isinstance(raw, Mapping):
            raise RegistryDeclarationError(
                f"registry entry must be a mapping, got {type(raw).__name__}"
            )
        registry = str(raw.get("registry") or "").strip().lower()
        if not registry:
            raise RegistryDeclarationError("registry entry is missing `registry`")
        if registry in normalised:
            raise RegistryDeclarationError(
                f"registry {registry!r} is declared twice - two mirrors for one upstream "
                f"cannot both be the answer, and picking one silently is a guess"
            )
        mirror = str(raw.get("mirror") or "").strip()
        if not mirror:
            raise RegistryDeclarationError(
                f"registry {registry!r}: `mirror` is required. An entry naming no mirror "
                f"declares nothing and would still read as a configured posture."
            )
        mechanism = str(raw.get("mechanism") or "").strip()
        if mechanism not in MECHANISMS:
            raise RegistryDeclarationError(
                f"registry {registry!r}: mechanism={mechanism!r} is not one of {list(MECHANISMS)}"
            )
        if mechanism == MECHANISM_DAEMON_MIRROR and registry != DOCKER_HUB:
            raise RegistryDeclarationError(
                f"registry {registry!r}: {MECHANISM_DAEMON_MIRROR} redirects DOCKER HUB "
                f"PULLS ONLY - the daemon's `registry-mirrors` setting does not intercept "
                f"{registry!r}. Declaring it here would report a clean air-gap verdict for "
                f"a deployment that still reaches the public internet on first use. "
                f"Re-host those images in the mirror and declare "
                f"{MECHANISM_REPOSITORY_REWRITE}."
            )
        normalised[registry] = {
            "registry": registry,
            "mirror": mirror,
            "mechanism": mechanism,
            "username_ref": _require_ref(registry, "username_ref", raw.get("username_ref")),
            "password_ref": _require_ref(registry, "password_ref", raw.get("password_ref")),
            "note": raw.get("note"),
        }
    return normalised


# ---------------------------------------------------------------------------
# What counts as internal - one definition, shared with the air-gap rules
# ---------------------------------------------------------------------------


def internal_host_suffixes(*, force: bool = False) -> list[str]:
    """``allowlist.internal_host_suffixes`` from ``args/twin_airgap_rules.yaml``.

    Read from THE air-gap rule config rather than restated here so this module
    and ``airgap-internal-registry`` cannot disagree about which hosts are
    inside the enclave.
    """
    global _SUFFIX_CACHE
    if _SUFFIX_CACHE is not None and not force:
        return _SUFFIX_CACHE
    try:
        cfg = _read_yaml(AIRGAP_RULES_PATH)
        suffixes = list((cfg.get("allowlist") or {}).get("internal_host_suffixes") or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "could not read internal host suffixes (%s) - treating none as internal", exc
        )
        suffixes = []
    _SUFFIX_CACHE = [str(s).lower() for s in suffixes]
    return _SUFFIX_CACHE


def is_internal_host(host: str, suffixes: Optional[Iterable[str]] = None) -> bool:
    """Is ``host`` inside the enclave, by the air-gap rules' own allowlist?

    The port is stripped first: a mirror is habitually written
    ``registry.internal.example.mil:5000`` and a suffix match against the raw
    string would miss ``.mil``.
    """
    low = str(host or "").strip().lower()
    if not low:
        return False
    if "://" in low:
        low = low.split("://", 1)[1]
    low = low.split("/", 1)[0]
    if low.count(":") == 1:
        low = low.rsplit(":", 1)[0]
    if not low:
        return False
    pool = [str(s).lower() for s in suffixes] if suffixes is not None else internal_host_suffixes()
    return any(low == s or low.endswith(s) for s in pool)


def registry_of(ref: str) -> str:
    """The registry host an image ref names, with Docker Hub as the default.

    ``postgres:16.3-alpine`` and ``valkey/valkey:8`` name no host and resolve to
    Docker Hub; ``public.ecr.aws/lambda/python:3.11`` names one. The
    discriminator is Docker's own: the first path component is a registry only
    when it contains a ``.`` or a ``:``, or is exactly ``localhost``.
    """
    text = str(ref or "").strip()
    if not text:
        return DOCKER_HUB
    head = text.split("/", 1)[0]
    if "/" in text and ("." in head or ":" in head or head == "localhost"):
        return head.lower()
    return DOCKER_HUB


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def pull_origin(ref: str, config: dict | None = None) -> dict[str, Any]:
    """Where would a pull of ``ref`` GO, and does it leave the enclave?

    ``external`` is the field callers act on:

      ``True``   the pull reaches a registry outside the enclave. THE FINDING,
                 and it is what a deployment with no declaration reports for
                 every image - identical to the flx-airgap-02 posture.
      ``False``  the pull is redirected to a mirror this system's own allowlist
                 calls internal. No public-internet dependency.

    ``verified`` is always ``False`` on a mirrored row and says so: nothing here
    contacts a registry, so what is established is that the pull is INTERNAL,
    never that the mirror holds the image.
    """
    cfg = config if config is not None else load_declaration()
    registry = registry_of(ref)
    row: dict[str, Any] = {
        "ref": str(ref),
        "registry": registry,
        "mirror": None,
        "mechanism": None,
        "origin": ORIGIN_NO_MIRROR,
        "external": True,
        "verified": False,
        "reason": "",
    }

    if not cfg.get("enabled", False):
        row["origin"] = ORIGIN_MIRROR_DISABLED
        row["reason"] = (
            f"registry declaration is disabled - {registry} is asked directly, which is "
            f"the flx-airgap-02 posture (the local cache is the only discriminator)"
        )
        return row

    entry = validate(cfg).get(registry)
    if entry is None:
        row["reason"] = (
            f"no mirror is declared for {registry}, so a pull of {ref} reaches it directly"
        )
        return row

    row["mirror"] = entry["mirror"]
    row["mechanism"] = entry["mechanism"]
    if not is_internal_host(entry["mirror"]):
        row["origin"] = ORIGIN_MIRROR_NOT_INTERNAL
        row["reason"] = (
            f"{registry} is mirrored at {entry['mirror']}, which is not an internal host by "
            f"args/twin_airgap_rules.yaml's own allowlist - the pull still leaves the enclave"
        )
        return row

    row["origin"] = ORIGIN_INTERNAL_MIRROR
    row["external"] = False
    row["reason"] = (
        f"{registry} is served by {entry['mirror']} via {entry['mechanism']} - the pull stays "
        f"inside the enclave. NOT a claim that the mirror holds this image: nothing here "
        f"contacts a registry, and mirror completeness is a separate question with a "
        f"separate repair (load the vendored bundle into the mirror)."
    )
    return row


def docker_host(
    config: dict | None = None, env: Optional[Mapping[str, str]] = None
) -> dict[str, Any]:
    """Which daemon does FLOCI talk to? Never how the host process reaches one.

    The environment wins over the declaration: compose reads the variable, and a
    YAML key cannot set a container's environment. The declaration's
    ``docker_host`` is what the variable SHOULD be on this deployment, recorded
    as data so the runbook and the compose file cannot drift.
    """
    src = env if env is not None else os.environ
    cfg = config if config is not None else load_declaration()
    declared = str(cfg.get("docker_host") or "").strip()
    from_env = str(src.get(DOCKER_HOST_ENV) or "").strip()
    effective = from_env or declared or DEFAULT_CONTAINER_DOCKER_HOST
    return {
        "env_var": DOCKER_HOST_ENV,
        "from_env": from_env or None,
        "declared": declared or None,
        "effective": effective,
        "remote": not effective.startswith("unix://"),
        "basis": "environment" if from_env else ("declaration" if declared else "compose default"),
        "note": (
            f"{DOCKER_HOST_ENV} is the daemon floci starts service containers on. It is NOT "
            f"FLOCI_DOCKER_SOCKET (read by emulator.docker_basis() for the HOST python "
            f"process) and NOT FLOCI_DOCKER_SOCKET_MOUNT (the compose bind-mount source)."
        ),
    }


def credential_refs(config: dict | None = None) -> list[dict[str, Any]]:
    """The declared credential REFERENCES. Never a value - nothing here resolves."""
    cfg = config if config is not None else load_declaration()
    out = []
    for entry in validate(cfg).values():
        out.append(
            {
                "registry": entry["registry"],
                "mirror": entry["mirror"],
                "mechanism": entry["mechanism"],
                "username_ref": entry["username_ref"],
                "password_ref": entry["password_ref"],
                "credentialed": bool(entry["username_ref"] or entry["password_ref"]),
            }
        )
    return out


def posture(config: dict | None = None) -> dict[str, Any]:
    """A one-shot summary for a report to embed beside its verdict."""
    cfg = config if config is not None else load_declaration()
    entries = validate(cfg)
    internal = {r: e for r, e in entries.items() if is_internal_host(e["mirror"])}
    return {
        "declared": bool(cfg.get("enabled", False)),
        "registries": sorted(entries),
        "internal_mirrors": sorted(internal),
        "external_mirrors": sorted(set(entries) - set(internal)),
        "docker_host": docker_host(cfg),
        "credentials": credential_refs(cfg),
        "reason": cfg.get("_reason"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="floci registry posture and per-registry credentials (flx-airgap-03)"
    )
    parser.add_argument("--show", action="store_true", help="print the declared posture")
    parser.add_argument(
        "--check", action="store_true", help="validate the declaration; exit 1 if unusable"
    )
    parser.add_argument(
        "--origins", action="store_true", help="pull origin for every declared runtime image"
    )
    parser.add_argument("--config", help="path to an alternate declaration")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        cfg = load_declaration(args.config, force=True)
    except RegistryDeclarationError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, indent=2) if args.json else f"REFUSED: {exc}")
        return 1

    if args.check:
        payload = {"ok": True, "registries": sorted(validate(cfg))}
        print(json.dumps(payload, indent=2) if args.json else "declaration is usable")
        return 0

    if args.origins:
        from tools.cloud import runtime_images

        rows = [pull_origin(img["ref"], cfg) for img in runtime_images.declared_images()]
        external = [r for r in rows if r["external"]]
        payload = {"images": rows, "external_count": len(external), "total": len(rows)}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for r in rows:
                label = "EXTERNAL" if r["external"] else "internal"
                print(f"  {label}  {r['ref']}  ({r['origin']})")
            print(f"\n{len(external)} of {len(rows)} would be pulled from OUTSIDE the enclave")
        return 0

    payload = posture(cfg)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"declared:         {payload['declared']}")
        print(
            f"docker_host:      {payload['docker_host']['effective']} "
            f"(basis: {payload['docker_host']['basis']})"
        )
        print(f"internal mirrors: {payload['internal_mirrors'] or '-'}")
        print(f"external mirrors: {payload['external_mirrors'] or '-'}")
        for cred in payload["credentials"]:
            print(
                f"  {cred['registry']} -> {cred['mirror']} "
                f"[{cred['mechanism']}] credentialed={cred['credentialed']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
