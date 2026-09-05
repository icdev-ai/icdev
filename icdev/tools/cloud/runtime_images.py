# CUI // SP-CTI
"""Which container images must ALREADY be cached for floci to run offline (flx-airgap-02).

THE DEFECT THIS EXISTS FOR
--------------------------
``tools/cloud/emulator.py`` documents that container-backed services (Lambda,
RDS, ElastiCache, OpenSearch, MSK, ECS/EC2/EKS) need a docker socket. Having a
socket is necessary and NOT sufficient: floci does not carry those runtimes
inside its own image — it **pulls a separate base image from the public
internet the first time each service is used**. On a disconnected high side
that pull fails at exactly the moment a demo runs, and nothing before this
module could say which images would have been fetched.

The enumeration lives in ``args/floci_runtime_images.yaml`` and every entry
there was MEASURED — ``docker events --filter type=image`` recorded while a
live floci was driven through each service. This module reads that declaration,
derives what a given design needs, and asks the LOCAL DAEMON whether it already
holds it.

    declared services  ->  required images  ->  cache state  ->  verdict

THE VERDICT IS FOUR-VALUED AND THE MIDDLE TWO ARE THE POINT
------------------------------------------------------------
``satisfied``      every required image is PROVEN present. Nothing will pull.
``blocked``        at least one is PROVEN absent. A run-time pull WILL be
                   attempted, and on a disconnected host it will fail.
``indeterminate``  a container-backed service is declared whose VARIANT could
                   not be resolved (a Lambda with no runtime named, an RDS with
                   no engine). We cannot say which image it needs. NOT clean.
``unmeasured``     the cache could not be read at all (no docker CLI, daemon
                   unreachable). NEVER a clean bill of health.

An empty declared-service set is ``satisfied`` with an empty requirement, and
that is honest: a design using no container-backed service pulls nothing.
``requirements`` is always reported beside the verdict so a caller can tell
"satisfied over 11 images" from "satisfied over 0".

PRESENCE IS A LADDER, AND CHECKING THE TAG ALONE IS A FABRICATED BLOCKER
------------------------------------------------------------------------
MEASURED 2026-09-05 on Docker 28.5.1. ``docker save memcached@sha256:75c9…``
followed by ``docker load`` on another host produces an image with
``RepoTags=[]`` **and** ``RepoDigests=[]`` — it resolves by NEITHER
``memcached:1.6`` NOR ``memcached@sha256:75c9…``, and ``docker image ls`` does
not list it. It resolves only by its image ID, which for that OCI-layout save
was byte-equal to the pinned manifest digest.

That is precisely how ``tools/airgap/image_vendor.py`` delivers a bundle to the
high side. So a presence check written against the tag would report ``absent``
for a correctly vendored, fully offline-capable host — refusing the one
deployment this whole card exists to serve. :func:`image_state` therefore tries
three rungs and REPORTS WHICH ONE ANSWERED:

    ``present_tagged``      the ref resolves and its RepoDigest matches the pin
    ``present_by_digest``   ``repo@sha256:…`` resolves
    ``present_by_id``       the pin's digest resolves as an image ID — the
                            ``image_vendor --load`` case measured above
    ``digest_mismatch``     the ref resolves but is a DIFFERENT image. Not
                            absent, and a different repair: re-vendor, don't
                            re-mirror.
    ``absent``              no rung answered
    ``unmeasured``          docker could not be asked

``present_by_id`` is not universal and is not claimed to be: an engine whose
image store does not index a digest-saved image by its manifest digest simply
will not answer that rung, exactly as ``image_vendor``'s own post-load check
reports ``cannot answer`` rather than a wrong answer.

NOTHING HERE PULLS
------------------
Every docker call goes through ``image_vendor._docker``, whose
``ALLOWED_DOCKER_COMMANDS`` frozenset contains no ``pull``. There is no second
subprocess door — a probe that could fetch what it is measuring would report a
green cache it had just created.

Usage::

    from tools.cloud import runtime_images

    report = runtime_images.evaluate({"resources": [{"type": "aws_lambda_function",
                                                     "runtime": "python3.11"}]})
    if report["state"] == runtime_images.STATE_BLOCKED:
        ...  # these will pull at run time

CLI::

    python -m tools.cloud.runtime_images --list --json
    python -m tools.cloud.runtime_images --check --json
    python -m tools.cloud.runtime_images --check --services lambda,rds --variants postgres
    python -m tools.cloud.runtime_images --measure-help
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.cloud.runtime_images")

BASE_DIR = repo_root(__file__)
DEFAULT_CONFIG_PATH = BASE_DIR / "args" / "floci_runtime_images.yaml"

# ── Verdicts. Never merged; see the module docstring. ──────────────────────
STATE_SATISFIED = "satisfied"
STATE_BLOCKED = "blocked"
STATE_INDETERMINATE = "indeterminate"
STATE_UNMEASURED = "unmeasured"

# ── Per-image cache states. ────────────────────────────────────────────────
PRESENT_TAGGED = "present_tagged"
PRESENT_BY_DIGEST = "present_by_digest"
PRESENT_BY_ID = "present_by_id"
DIGEST_MISMATCH = "digest_mismatch"
ABSENT = "absent"
UNMEASURED = "unmeasured"

#: The rungs that mean "this host can start the container with no network".
PRESENT_STATES = frozenset({PRESENT_TAGGED, PRESENT_BY_DIGEST, PRESENT_BY_ID})

_CONFIG_CACHE: dict | None = None


# ---------------------------------------------------------------------------
# Declaration
# ---------------------------------------------------------------------------


def load_declaration(path: str | Path | None = None, *, force: bool = False) -> dict:
    """Load (and cache) ``args/floci_runtime_images.yaml``.

    A missing or unreadable file yields a DISABLED declaration with no images.
    Disabled means "this deployment makes no claim", which callers must not
    read as "nothing is required" — :func:`evaluate` reports ``unmeasured``
    for it rather than ``satisfied``.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and path is None and not force:
        return _CONFIG_CACHE
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        import yaml

        with open(cfg_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("floci runtime-image declaration not found at %s", cfg_path)
        cfg = {"enabled": False, "images": [], "_reason": f"declaration not found: {cfg_path}"}
    except Exception as exc:  # noqa: BLE001
        logger.error("failed to load floci runtime-image declaration (%s)", exc)
        cfg = {"enabled": False, "images": [], "_reason": f"declaration unreadable: {exc}"}
    if path is None:
        _CONFIG_CACHE = cfg
    return cfg


def declared_images(config: dict | None = None) -> list[dict]:
    """Every measured image row, normalized."""
    cfg = config if config is not None else load_declaration()
    rows: list[dict] = []
    for raw in cfg.get("images", []) or []:
        if not isinstance(raw, dict) or not raw.get("ref"):
            continue
        rows.append(
            {
                "ref": str(raw["ref"]),
                "digest": raw.get("digest"),
                "service": str(raw.get("service", "")).lower(),
                "variant": raw.get("variant"),
                "mutable_tag": bool(raw.get("mutable_tag", False)),
                "note": raw.get("note"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Deriving what a design needs
# ---------------------------------------------------------------------------


def _iter_strings(obj: Any) -> Iterable[str]:
    """Every string value in a nested structure, including dict KEYS.

    Keys matter here in a way they do not for the host matcher in
    ``twin_core.airgap_rules``: an IaC plan spells a resource type as a key at
    least as often as a value (``{"aws_lambda_function": {...}}``).
    """
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            yield from _iter_strings(v)


def _token_hit(token: str, haystack: str) -> bool:
    """Word-boundary, case-insensitive match.

    Boundaries matter: a bare ``ec2`` substring match would fire on
    ``sec2ion``, and ``es`` (an OpenSearch alias) would fire on every English
    word containing it — which is why ``es`` is deliberately absent from the
    declared tokens and ``elasticsearch`` is used instead.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack) is not None


def declared_services(target: Any, config: dict | None = None) -> set[str]:
    """Which container-backed services does ``target`` declare?

    Deliberately over- rather than under-inclusive: a false positive costs a
    vendored image nobody needed, a false negative costs a failed demo on a
    disconnected host.
    """
    cfg = config if config is not None else load_declaration()
    tokens: dict[str, list[str]] = cfg.get("service_tokens", {}) or {}
    blob = "\n".join(_iter_strings(target)).lower()
    found = {svc for svc, toks in tokens.items() if any(_token_hit(t.lower(), blob) for t in toks)}
    # `implies` is DATA (args/floci_runtime_images.yaml), not a hard-coded rule:
    # ECS and EKS bring floci's ECR registry container up alongside them.
    implies: dict[str, list[str]] = cfg.get("implies", {}) or {}
    for svc in sorted(found):
        found |= {str(x).lower() for x in implies.get(svc, []) or []}
    return found


def declared_variants(target: Any, service: str, config: dict | None = None) -> set[str]:
    """Which variants of ``service`` does ``target`` declare? Possibly empty."""
    cfg = config if config is not None else load_declaration()
    table: dict[str, dict[str, list[str]]] = cfg.get("variant_tokens", {}) or {}
    per_service = table.get(service, {}) or {}
    blob = "\n".join(_iter_strings(target)).lower()
    return {
        variant
        for variant, toks in per_service.items()
        if any(_token_hit(t.lower(), blob) for t in toks)
    }


def images_for(
    services: Iterable[str],
    *,
    variants: Optional[Iterable[str]] = None,
    config: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """Required images for ``services``, plus the services whose variant is undetermined.

    Returns ``(images, variant_undetermined)``.

    A service that declares variants in the table but for which none was
    resolved contributes NO images and IS named in the second list. That is the
    honest answer: we do not know whether it needs the python or the nodejs
    base, and picking one would either fabricate a blocker or fabricate a clean
    bill. A service with no variants in the table (OpenSearch, MSK, EC2, EKS,
    ECR) has exactly one image and is never undetermined.
    """
    cfg = config if config is not None else load_declaration()
    rows = declared_images(cfg)
    wanted = {str(s).lower() for s in services}
    variant_set = {str(v) for v in variants} if variants is not None else None
    variant_table: dict[str, dict] = cfg.get("variant_tokens", {}) or {}

    chosen: list[dict] = []
    undetermined: list[str] = []
    for svc in sorted(wanted):
        svc_rows = [r for r in rows if r["service"] == svc]
        if not svc_rows:
            continue
        has_variants = any(r["variant"] for r in svc_rows)
        if not has_variants:
            chosen.extend(svc_rows)
            continue
        if variant_set is None:
            undetermined.append(svc)
            continue
        matched = [r for r in svc_rows if r["variant"] in variant_set]
        if matched:
            chosen.extend(matched)
        elif svc in variant_table:
            undetermined.append(svc)
    # Stable, de-duplicated by ref.
    seen: set[str] = set()
    deduped = []
    for row in chosen:
        if row["ref"] not in seen:
            seen.add(row["ref"])
            deduped.append(row)
    return deduped, sorted(set(undetermined))


# ---------------------------------------------------------------------------
# Cache probe — reuses image_vendor's ONE allowlisted docker door
# ---------------------------------------------------------------------------


def _vendor():
    from tools.airgap import image_vendor

    return image_vendor


def docker_available() -> dict[str, Any]:
    """Can the local daemon be asked at all? Delegates to ``image_vendor``."""
    try:
        return dict(_vendor().docker_basis())
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"docker probe unusable: {exc}"}


def _repo_digests(ref: str) -> tuple[bool, list[str]]:
    """``(resolved, RepoDigests)`` for ``ref``. Never pulls."""
    proc = _vendor()._docker(
        ["image", "inspect", ref, "--format", "{{json .RepoDigests}}"], timeout=60
    )
    if proc.returncode != 0:
        return False, []
    try:
        parsed = json.loads(proc.stdout.strip() or "null")
    except ValueError:
        return True, []
    return True, ([str(x) for x in parsed] if isinstance(parsed, list) else [])


def repo_of(ref: str) -> str:
    """The repository half of ``ref``, tag stripped.

    A tag is the trailing ``:x`` only when that segment holds no ``/`` — so
    ``localhost:5000/foo`` keeps its port and ``postgres:16.3-alpine`` loses
    its tag.
    """
    head, sep, tail = ref.rpartition(":")
    return head if sep and "/" not in tail else ref


def image_state(image: Mapping[str, Any]) -> dict[str, Any]:
    """Cache state for ONE declared image. See the ladder in the module docstring."""
    ref = str(image["ref"])
    digest = image.get("digest")
    result: dict[str, Any] = {"ref": ref, "digest": digest, "state": ABSENT, "basis": None}
    repo = repo_of(ref)

    try:
        resolved, repo_digests = _repo_digests(ref)
        if resolved:
            if not digest:
                # Nothing pinned to compare against: resolving IS the answer.
                result.update(state=PRESENT_TAGGED, basis="ref resolved; declaration pins no digest")
                return result
            if any(rd.endswith("@" + digest) for rd in repo_digests):
                result.update(state=PRESENT_TAGGED, basis="ref resolved with matching RepoDigest")
                return result
            if repo_digests:
                result.update(
                    state=DIGEST_MISMATCH,
                    basis=f"ref resolved but carries {repo_digests[0]}, not the pinned {digest}",
                )
                return result
            # Resolved with NO RepoDigest: a locally built or loaded image
            # wearing this tag. Fall through to the digest rungs rather than
            # calling it present — the tag is not evidence of content.

        if digest:
            by_digest = f"{repo}@{digest}"
            if _vendor().local_image_present(by_digest).get("present"):
                result.update(state=PRESENT_BY_DIGEST, basis=f"{by_digest} resolved")
                return result
            # The `image_vendor --load` case, MEASURED 2026-09-05: a
            # digest-saved bundle resolves by image ID only.
            if _vendor().local_image_present(digest).get("present"):
                result.update(
                    state=PRESENT_BY_ID,
                    basis=f"{digest} resolved as an image ID (loaded bundle, no tag)",
                )
                return result

        result.update(state=ABSENT, basis="no tag, digest or image-id rung resolved")
        return result
    except FileNotFoundError:
        result.update(state=UNMEASURED, basis="docker CLI not found on PATH")
        return result
    except Exception as exc:  # noqa: BLE001
        result.update(state=UNMEASURED, basis=f"docker probe failed: {exc}")
        return result


def cache_states(
    images: Iterable[Mapping[str, Any]],
    *,
    prober: Optional[Callable[[Mapping[str, Any]], dict]] = None,
) -> list[dict]:
    """Cache state for each image.

    ``prober`` is injectable so a test can state a cache instead of depending
    on whatever this host happens to hold — a test asserting on the live cache
    would pass or fail on a `docker rmi` nobody in the test ran.
    """
    if prober is None:
        basis = docker_available()
        if not basis.get("available"):
            reason = basis.get("reason", "docker unavailable")
            return [
                {"ref": str(i["ref"]), "digest": i.get("digest"), "state": UNMEASURED,
                 "basis": reason}
                for i in images
            ]
        prober = image_state
    return [dict(prober(i)) for i in images]


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


def evaluate(
    target: Any = None,
    *,
    services: Optional[Iterable[str]] = None,
    variants: Optional[Iterable[str]] = None,
    config: dict | None = None,
    prober: Optional[Callable[[Mapping[str, Any]], dict]] = None,
) -> dict[str, Any]:
    """Would this deployment need an EXTERNAL PULL at run time?

    Pass a ``target`` (design graph / IaC plan) to derive services and variants
    from it, or pass ``services``/``variants`` explicitly.
    """
    cfg = config if config is not None else load_declaration()
    report: dict[str, Any] = {
        "state": STATE_UNMEASURED,
        "emulator": cfg.get("emulator", "floci"),
        "declaration_enabled": bool(cfg.get("enabled", False)),
        "measured_on": cfg.get("measured_on"),
        "services": [],
        "variant_undetermined": [],
        "requirements": [],
        "missing": [],
        "present": [],
        "unmeasured": [],
        "mismatched": [],
        "reason": None,
    }

    if not cfg.get("enabled", False):
        report["reason"] = cfg.get(
            "_reason", "runtime-image declaration is disabled — no claim is made about the cache"
        )
        return report

    if services is None:
        if target is None:
            report["reason"] = "no target and no service list supplied"
            return report
        services = declared_services(target, cfg)
        if variants is None:
            variants = set()
            for svc in services:
                variants |= declared_variants(target, svc, cfg)

    services = sorted({str(s).lower() for s in services})
    report["services"] = services

    required, undetermined = images_for(services, variants=variants, config=cfg)
    report["requirements"] = [r["ref"] for r in required]
    report["variant_undetermined"] = undetermined

    if not required and not undetermined:
        # Honest: nothing container-backed is declared, so nothing will pull.
        report["state"] = STATE_SATISFIED
        report["reason"] = "no container-backed service declared"
        return report

    states = cache_states(required, prober=prober)
    report["missing"] = [s for s in states if s["state"] == ABSENT]
    report["mismatched"] = [s for s in states if s["state"] == DIGEST_MISMATCH]
    report["unmeasured"] = [s for s in states if s["state"] == UNMEASURED]
    report["present"] = [s for s in states if s["state"] in PRESENT_STATES]

    # Ordered worst-first. A blocked image is a proven finding and outranks a
    # variant we could not resolve, which outranks a cache we could not read.
    if report["missing"] or report["mismatched"]:
        report["state"] = STATE_BLOCKED
        report["reason"] = (
            f"{len(report['missing'])} required image(s) absent from the local cache, "
            f"{len(report['mismatched'])} present at a different digest — "
            f"these WILL be pulled at run time"
        )
    elif report["unmeasured"]:
        report["state"] = STATE_UNMEASURED
        report["reason"] = report["unmeasured"][0].get("basis") or "cache could not be read"
    elif undetermined:
        report["state"] = STATE_INDETERMINATE
        report["reason"] = (
            "cannot determine which base image these declare: "
            + ", ".join(undetermined)
        )
    else:
        report["state"] = STATE_SATISFIED
        report["reason"] = f"all {len(required)} required image(s) already cached"
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_MEASURE_HELP = """\
How the table in args/floci_runtime_images.yaml was produced, and how to redo it.

  1. Record what the DAEMON fetches, in another shell:
       docker events --filter type=image --format '{{.Time}} {{.Action}} {{.Actor.ID}}'

  2. Start the emulator (it must have the docker socket mounted):
       docker compose --profile floci up -d floci
       curl -s http://127.0.0.1:4566/_localstack/health

  3. Drive EVERY container-backed service with boto3 against
     http://127.0.0.1:4566 -- create AND use, because creation alone does not
     always start a container. Lambda must be INVOKED, not merely created.
     Vary the runtime/engine: the image set is a function of declared
     configuration, not of the service.

  4. Each `pull` line the daemon logged is a `ref`. Record its digest with
       docker image inspect <ref> --format '{{index .RepoDigests 0}}'

  5. Do NOT record an image the emulator pulled because YOUR OWN workload named
     it (an ECS task definition's image). That is not a floci runtime base.

Do not add a row you did not observe. A digest nobody measured is exactly the
fabrication vendor/images/README.md was written against, and it would be
believed.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="floci runtime base images required for offline operation (flx-airgap-02)"
    )
    parser.add_argument("--list", action="store_true", help="print the measured declaration")
    parser.add_argument("--check", action="store_true", help="probe the local cache")
    parser.add_argument("--services", help="comma-separated service list (default: all declared)")
    parser.add_argument("--variants", help="comma-separated variant list")
    parser.add_argument("--measure-help", action="store_true", help="how the table was measured")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.measure_help:
        print(_MEASURE_HELP)
        return 0

    cfg = load_declaration()

    if args.list or not args.check:
        rows = declared_images(cfg)
        if args.json:
            print(json.dumps({"measured_on": cfg.get("measured_on"), "images": rows}, indent=2))
        else:
            print(f"floci runtime base images (measured {cfg.get('measured_on')}):")
            for r in rows:
                tag = "  [MUTABLE TAG]" if r["mutable_tag"] else ""
                variant = f"/{r['variant']}" if r["variant"] else ""
                print(f"  {r['service']}{variant:<14} {r['ref']}{tag}")
        if not args.check:
            return 0

    services = (
        [s.strip() for s in args.services.split(",") if s.strip()]
        if args.services
        else sorted({r["service"] for r in declared_images(cfg)})
    )
    variants = (
        [v.strip() for v in args.variants.split(",") if v.strip()]
        if args.variants
        else sorted({r["variant"] for r in declared_images(cfg) if r["variant"]})
    )
    report = evaluate(services=services, variants=variants, config=cfg)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nstate: {report['state']} -- {report['reason']}")
        for row in report["missing"] + report["mismatched"]:
            print(f"  MISSING  {row['ref']}  ({row['basis']})")
        for row in report["present"]:
            print(f"  present  {row['ref']}  ({row['state']})")
        for row in report["unmeasured"]:
            print(f"  ?        {row['ref']}  ({row['basis']})")

    # 0 satisfied / 1 blocked / 2 could not measure. `unmeasured` is NOT clean,
    # so it must not exit 0 -- the same rule image_vendor --verify follows.
    return {STATE_SATISFIED: 0, STATE_BLOCKED: 1, STATE_INDETERMINATE: 1}.get(report["state"], 2)


if __name__ == "__main__":
    sys.exit(main())
