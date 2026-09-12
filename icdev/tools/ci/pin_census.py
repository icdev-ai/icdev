#!/usr/bin/env python3
# CUI // SP-CTI
"""Census of UNPINNED supply-chain references in CI (xrv-route-03).

WHY THIS EXISTS
---------------
Four different things in this repository decide, at run time, which bytes a
build executes — and none of them was checked:

  * ``.gitlab-ci.yml:148`` runs ``pip install --quiet llm-sandbox docker pyyaml``
    with no version on any of the three. The ``reverse-skill`` external review
    fails CI on exactly that shape, because a job that installs today's
    ``llm-sandbox`` is a job whose behaviour changes when somebody else
    publishes a release.
  * Every ``uses:`` in ``.github/workflows/*.yml`` is TAG-pinned
    (``actions/checkout@v4``), not SHA-pinned. A git tag is mutable: whoever
    controls the action repository can move ``v4`` to any commit, and every
    workflow here runs it with the token the job was given.
  * ``docker-compose.yml`` names four ``floci/*`` emulator images by TAG, and
    only the AWS one has a measured digest in ``vendor/images/``. A tag is the
    same mutability one layer down.
  * ``curl -fsSL https://ollama.com/install.sh | sh`` executes whatever that URL
    serves at the moment the job runs.

WHAT IS AND IS NOT A SITE
-------------------------
The finding is A REFERENCE THAT DOES NOT NAME THE BYTES IT RESOLVES TO. It is
NOT "an install". Four predicates keep this high-signal, and every one of them
is RE-DERIVED on each run rather than kept as an exemption list — an exemption
list is a claim a reviewer must check, a predicate is one the scanner proves:

  * ``pip install -r requirements.txt`` / ``-e .`` / ``dist/*.whl`` names no
    package literal at all. The pin lives in the declared file (or in the wheel
    vendoring), so there is nothing here to pin and it is not a site.
  * ``npm ci`` and a bare ``npm install`` resolve through ``package-lock.json``,
    which IS the pin — ``npm ci`` refuses to run without one. Only
    ``npm install -g <pkg>`` names a package with no lockfile behind it.
  * A compose service carrying a ``build:`` key BUILDS its image from a
    Dockerfile in this tree. It is never pulled, so a registry digest cannot
    describe it and vendoring it is meaningless. Measured on adoption: 21 of the
    29 services in ``docker-compose.yml`` are built here, so this one predicate
    is the difference between 8 real sites and 29 mostly-noise ones.
  * ``uses: ./.github/workflows/x.yaml`` is a path into this repository, not a
    third-party action. It is pinned by the commit under review.

A site stops being a site by naming the bytes: ``pkg==1.2.3``,
``owner/repo@<40-hex>``, ``repo@sha256:<64-hex>``, or — for an image — a digest
line in ``vendor/images/*.txt``, which is where this repository already records a
MEASURED digest. ``tools/airgap/image_vendor.parse_pin`` is IMPORTED, never
re-implemented: one spelling of "what is a digest pin", or the vendor and the
gate come to disagree about a fact neither of them changed.

KEYS CARRY NO LINE NUMBER AND NO REF
------------------------------------
The key is ``<file>::<kind>::<subject>``. Two deliberate omissions:

  * NO LINE NUMBER, for the reason ``undeclared_import_census`` gives: line
    numbers churn on every edit above the site, which would make the census a
    merge-conflict generator and every unrelated PR a census edit.
  * NO REF. ``actions/checkout@v4`` -> ``@v5`` is a routine bump and the SAME
    unpinned decision; keying on the ref would fail ``--check`` on every bump
    and demand a census edit that says nothing new. The ref is REPORTED, so a
    reader sees it; it is just not identity.

So ``.gitlab-ci.yml`` installing ``ruff`` at two different lines is ONE site —
one decision, taken once.

SURVEYED, NOT GATED
-------------------
``requirements.txt`` declares 46 distributions and 45 of them use ``>=``. That
is a RANGE, not a pin, and it is reported under ``surveyed_not_gated`` with its
reason rather than gated: the install-time pin for a deployment is the vendored
wheel set (``tools/airgap/wheel_vendor.py``), and refusing 45 ranges would
refuse routine work — the defect the PreToolUse fire-rate survey found. Quoting
the number is what lets a later card decide with evidence instead of by feel.

CENSUS DISCIPLINE (same as args/ci_test_backlog.txt and args/ci_skip_census.txt)
-------------------------------------------------------------------------------
The census ENUMERATES sites by name. It does not count them. A bare count can be
held constant while the set churns — delete one site, add another, count
unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. ``pin_census.pin_max`` in ``args/pin_gate.yaml`` is a ceiling on the
REGISTERED count and MAY ONLY GO DOWN. Never raise it to get a commit through.

UNMEASURABLE IS ITS OWN VERDICT. A compose file that will not parse, a
``vendor/images`` directory that cannot be read, a CI file that will not
decode — each is reported BY NAME under ``unmeasurable`` and makes ``ok`` False.
"I could not look" must never render as "I looked and found nothing", which is
the whole reason this family of gates exists.

USAGE
-----
    python tools/ci/pin_census.py --check          # the gate; exit 1 on a NEW site
    python tools/ci/pin_census.py --json
    python tools/ci/pin_census.py --changed .gitlab-ci.yml --check
    python tools/ci/pin_census.py --staged
    python tools/ci/pin_census.py --prune          # drop entries whose site is gone
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence


def _find_repo_root(start: Path) -> Path:
    """Walk up to the checkout root rather than counting parents.

    This module is mirrored to ``icdev/tools/ci/``, where a fixed ``parents[2]``
    resolves to ``<repo>/icdev`` and every path below it is wrong. Resolved from
    ``__file__`` and never from ``os.getcwd()``, which is the worktree root under
    a git worktree (see CLAUDE.md).
    """
    for candidate in (start, *start.parents):
        if (candidate / "requirements.txt").exists() and (candidate / "args").is_dir():
            return candidate
    return start.parents[2]


REPO = _find_repo_root(Path(__file__).resolve().parent)
GATE_FILE = REPO / "args" / "pin_gate.yaml"

#: Site kinds. Each one sends a reader to a DIFFERENT repair, so they are never
#: merged into one "unpinned" bucket.
KIND_INSTALL = "unpinned_install"        # a package literal with no version pin
KIND_SCRIPT = "unpinned_script"          # curl/wget piped into a shell
KIND_ACTION = "tag_pinned_action"        # uses: owner/repo@<non-sha>
KIND_IMAGE = "undigested_image"          # compose image: with no vendored digest
KINDS = (KIND_INSTALL, KIND_SCRIPT, KIND_ACTION, KIND_IMAGE)


class PinCensusError(RuntimeError):
    """The census could not be produced. Never the same as a clean census."""


# ── pinned-ness predicates ─────────────────────────────────────────────────
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
#: ``==`` and ``===`` are pins. ``>=``, ``<=``, ``~=``, ``>``, ``<``, ``!=`` are
#: RANGES: they name a SET of releases, so the bytes a job runs still move.
_PIN_SPECIFIER = re.compile(r"===?[^=]")


def requirement_is_pinned(token: str) -> bool:
    """True when this pip requirement names the bytes it resolves to.

    ``pkg==1.2.3`` and ``pkg===1.2.3`` do. A PEP 508 direct reference does only
    when its ref is a full commit sha or an image digest — ``@v0.2.0`` is a TAG,
    and a tag can be moved to any commit by whoever owns the repository.
    """
    text = token.strip().strip("'\"")
    if not text:
        return False
    if _DIGEST.search(text):
        return True
    if "@" in text:
        return bool(_SHA40.match(text.rsplit("@", 1)[1].strip()))
    return bool(_PIN_SPECIFIER.search(text))


def action_is_pinned(ref: str) -> bool:
    """True only for a 40-hex commit sha. A tag and a branch are both mutable."""
    return bool(_SHA40.match(ref.strip().strip("'\"")))


def requirement_name(token: str) -> str:
    """The distribution name a pip token names, for the census key."""
    text = token.strip().strip("'\"")
    text = re.split(r"[\s;\[]", text, maxsplit=1)[0]
    return re.split(r"[<>=!~@]", text, maxsplit=1)[0].strip().lower()


def image_repo(ref: str) -> str:
    """The repository half of an image reference — no tag, no digest."""
    text = ref.strip().strip("'\"")
    if "@" in text:
        return text.split("@", 1)[0]
    # A registry host may carry a port (``host:5000/repo:tag``), so only a colon
    # in the LAST path segment is a tag separator.
    head, _, tail = text.rpartition("/")
    if ":" in tail:
        tail = tail.split(":", 1)[0]
    return f"{head}/{tail}" if head else tail


# ── the vendored-digest side ───────────────────────────────────────────────
def vendored_repos(repo: Path = REPO) -> tuple[frozenset[str], list[str]]:
    """Every image repo carrying a MEASURED digest line in vendor/images/*.txt.

    Returns ``(repos, unmeasurable)``. A directory that cannot be read is
    REPORTED, never treated as "no image is pinned" — that direction invents a
    finding for every image on any deployment whose vendor tree is absent.
    """
    try:
        from tools.airgap.image_vendor import parse_pin  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return frozenset(), [
            f"vendor digests unreadable: image_vendor import failed ({exc})"
        ]

    images_dir = repo / "vendor" / "images"
    if not images_dir.is_dir():
        return frozenset(), [
            f"vendor digests unreadable: {images_dir.as_posix()} is absent"
        ]

    found: set[str] = set()
    unmeasurable: list[str] = []
    for path in sorted(images_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            unmeasurable.append(f"vendor digests unreadable: {path.name} ({exc})")
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                found.add(parse_pin(line)["repo"])
            except ValueError:
                # A non-digest line in a pin file is image_vendor's own gate to
                # refuse (read_pins raises on it). Not this census's finding.
                continue
    return frozenset(found), unmeasurable


# ── shell-command scanning ─────────────────────────────────────────────────
#: Tokens that end one command and begin another.
_OPERATORS = frozenset({"&&", "||", ";", "|", "&"})
#: A redirection, with or without a leading file descriptor (``2>/dev/null``).
_REDIRECTION = re.compile(r"^[0-9&]?(?:>>?|<)")

_PIP_INSTALL = re.compile(
    r"(?:^|[\s;&|(])(?:python[0-9.]*\s+-m\s+)?pip[0-9.]*\s+install\b(?P<rest>[^\n]*)"
)
_NPM_INSTALL = re.compile(
    r"(?:^|[\s;&|(])npm\s+(?:install|i|add)\b(?P<rest>[^\n]*)"
)
_PIPE_TO_SHELL = re.compile(
    r"(?:^|[\s;&|(])(?P<fetch>curl|wget)\s+(?P<args>[^|\n]*)\|\s*(?:sudo\s+)?(?:ba|z|k|d)?sh\b"
)
_URL = re.compile(r"https?://[^\s'\"|;]+")
_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)")


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Join trailing-backslash continuations, keeping the FIRST line number.

    ``docker/Dockerfile.iac`` spells its install as ``pip install --no-cache-dir
    \\`` followed by ``ansible``, ``boto3``, ``botocore`` on their own lines. A
    per-physical-line scan sees a ``pip install`` naming nothing and reports no
    site — a clean answer to a question that was never asked.
    """
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 1
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.rstrip()
        if not buf:
            start = lineno
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        out.append((start, " ".join(part.strip() for part in buf)))
        buf = []
    if buf:
        out.append((start, " ".join(part.strip() for part in buf)))
    return out


def _tokens(segment: str) -> list[str]:
    """Split a command's argument text into tokens, quotes respected.

    ``pip install "icdev-core @ git+https://…@v0.2.0"`` is ONE requirement and a
    whitespace split would tear it into three. An unbalanced quote (common in a
    CI file carrying ``${{ }}`` templating) falls back to a whitespace split
    rather than dropping the whole line.
    """
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _arguments(rest: str) -> list[str]:
    """This command's own argument tokens — everything before the next operator.

    Truncated on TOKENS and never on the raw text, because a version specifier
    contains the same characters a redirection does. Measured on the first run:
    cutting the raw string at the first ``>`` turned ``pip install
    "boto3>=1.34"`` into a package called ``boto`` — a finding about a
    distribution that does not exist, which is worse than the one it hid.
    """
    out: list[str] = []
    for token in _tokens(rest):
        if token in _OPERATORS or _REDIRECTION.match(token):
            break
        out.append(token)
    return out


#: pip flags that CONSUME the following token, so that token is not a package.
_PIP_VALUE_FLAGS = frozenset({
    "-r", "--requirement", "-c", "--constraint", "-e", "--editable",
    "-t", "--target", "-i", "--index-url", "--extra-index-url", "-f",
    "--find-links", "--prefix", "--root", "--python-version", "--platform",
    "--abi", "--implementation", "--report", "--proxy", "--cert",
    "--client-cert", "--log", "--timeout", "--retries", "--exists-action",
    "--build", "--src", "--no-binary", "--only-binary",
})


#: PEP 508 direct reference — ``name @ <url>``. It CONTAINS a URL, so the
#: path heuristic below would discard it; and it is precisely the shape
#: ``icdev-core @ git+https://…@v0.2.0`` uses, whose ``@v0.2.0`` is a mutable
#: git TAG. Measured: without this the one direct reference in this tree was
#: silently dropped as "a path", which is the quiet miss a census exists to
#: prevent.
_DIRECT_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?\s*@\s*\S+")


def _looks_like_path(token: str) -> bool:
    """A path, a variable or a redirection is not a package name."""
    if token in {".", "..", "-"}:
        return True
    if _DIRECT_REFERENCE.match(token):
        return False
    if token.startswith(("/", "./", "../", "~", "$", "%", "2>", "1>", "&>")):
        return True
    if "/" in token or "\\" in token:
        return True
    return token.endswith((".whl", ".txt", ".tar.gz", ".zip", ".cfg", ".toml"))


def pip_requirements(rest: str) -> list[str]:
    """The package literals a ``pip install`` command names, pins included."""
    out: list[str] = []
    skip_next = False
    for token in _arguments(rest):
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if "=" not in token and token in _PIP_VALUE_FLAGS:
                skip_next = True
            continue
        if _looks_like_path(token):
            continue
        out.append(token)
    return out


def npm_global_packages(rest: str) -> list[str]:
    """Packages an ``npm install -g`` names.

    A LOCAL ``npm install`` / ``npm ci`` resolves through ``package-lock.json``,
    which IS the pin — ``npm ci`` refuses to run without one. So only the global
    form, which has no lockfile behind it, is in scope.
    """
    tokens = _arguments(rest)
    if not any(t in {"-g", "--global", "--location=global"} for t in tokens):
        return []
    return [t for t in tokens if not t.startswith("-") and not _looks_like_path(t)]


def npm_package_is_pinned(token: str) -> bool:
    """``pkg@1.2.3`` is pinned; ``pkg`` and ``pkg@latest`` are not."""
    text = token.strip().strip("'\"")
    scoped = text.startswith("@")
    body = text[1:] if scoped else text
    if "@" not in body:
        return False
    return bool(re.match(r"^\d+\.\d+", body.rsplit("@", 1)[1]))


# ── per-file scanners ──────────────────────────────────────────────────────
def scan_commands(rel: str, text: str) -> list[dict]:
    """Install and fetch-pipe-shell sites in one CI file's shell commands."""
    sites: list[dict] = []
    for lineno, line in logical_lines(text):
        for match in _PIP_INSTALL.finditer(line):
            for token in pip_requirements(match.group("rest")):
                if requirement_is_pinned(token):
                    continue
                name = requirement_name(token)
                if not name:
                    continue
                sites.append({
                    "kind": KIND_INSTALL,
                    "key": f"{rel}::{KIND_INSTALL}::{name}",
                    "file": rel, "line": lineno, "subject": name,
                    "reference": token.strip().strip("'\""), "manager": "pip",
                })
        for match in _NPM_INSTALL.finditer(line):
            for token in npm_global_packages(match.group("rest")):
                if npm_package_is_pinned(token):
                    continue
                name = token.strip().strip("'\"").lower()
                sites.append({
                    "kind": KIND_INSTALL,
                    "key": f"{rel}::{KIND_INSTALL}::{name}",
                    "file": rel, "line": lineno, "subject": name,
                    "reference": name, "manager": "npm-global",
                })
        for match in _PIPE_TO_SHELL.finditer(line):
            url = _URL.search(match.group("args"))
            subject = url.group(0) if url else match.group("fetch")
            sites.append({
                "kind": KIND_SCRIPT,
                "key": f"{rel}::{KIND_SCRIPT}::{subject}",
                "file": rel, "line": lineno, "subject": subject,
                "reference": subject, "manager": match.group("fetch"),
            })
    return sites


def scan_actions(rel: str, text: str) -> list[dict]:
    """Every ``uses:`` naming a third-party action by something other than a sha."""
    sites: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        match = _USES.match(raw)
        if not match:
            continue
        ref = match.group("ref").strip().strip("'\"")
        if ref.startswith((".", "/")):
            # A path into THIS repository: pinned by the commit under review.
            continue
        if "@" not in ref:
            # No ref at all. Not a tag pin, and inventing a verdict for it would
            # be guessing at a shape nothing in this tree uses.
            continue
        action, _, version = ref.rpartition("@")
        if action_is_pinned(version):
            continue
        sites.append({
            "kind": KIND_ACTION,
            "key": f"{rel}::{KIND_ACTION}::{action}",
            "file": rel, "line": lineno, "subject": action,
            "reference": ref, "manager": "github-actions",
        })
    return sites


def scan_compose(
    rel: str, text: str, digested: Iterable[str]
) -> tuple[list[dict], list[str]]:
    """Pulled images in one compose file with no vendored digest.

    A service carrying ``build:`` is skipped: it is BUILT from a Dockerfile in
    this tree, never pulled, so a registry digest cannot describe it.
    """
    try:
        import yaml  # noqa: PLC0415 — pyyaml IS declared in requirements.txt
    except ImportError as exc:  # pragma: no cover
        return [], [f"{rel}: pyyaml unavailable ({exc}) — compose images NOT measured"]
    try:
        document = yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001
        return [], [f"{rel}: will not parse ({exc}) — compose images NOT measured"]
    if not isinstance(document, dict):
        return [], [f"{rel}: is not a mapping — compose images NOT measured"]

    services = document.get("services") or {}
    if not isinstance(services, dict):
        return [], [f"{rel}: `services` is not a mapping — compose images NOT measured"]

    known = set(digested)
    sites: list[dict] = []
    for name, spec in sorted(services.items()):
        if not isinstance(spec, dict):
            continue
        ref = spec.get("image")
        if not isinstance(ref, str) or not ref.strip():
            continue
        if spec.get("build"):
            continue
        if _DIGEST.search(ref):
            continue
        repo = image_repo(ref)
        if repo in known:
            continue
        sites.append({
            "kind": KIND_IMAGE,
            "key": f"{rel}::{KIND_IMAGE}::{repo}",
            "file": rel, "line": 0, "subject": repo,
            "reference": ref.strip(), "manager": "compose", "service": str(name),
        })
    return sites, []


# ── config ─────────────────────────────────────────────────────────────────
DEFAULT_COMMAND_GLOBS = (
    ".github/workflows/*.yml", ".github/workflows/*.yaml",
    ".gitlab-ci.yml", "Dockerfile*", "docker/Dockerfile*",
)
DEFAULT_ACTION_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml")
DEFAULT_COMPOSE_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml")


def load_config(root: Optional[Path] = None) -> dict:
    """Read ``args/pin_gate.yaml``. A missing or unreadable gate is FATAL.

    Not a default: a gate that silently falls back to built-in settings cannot
    be told apart from one whose config was deleted.
    """
    path = (root / "args" / "pin_gate.yaml") if root else GATE_FILE
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise PinCensusError(f"pyyaml is required and declared ({exc})") from exc
    if not path.exists():
        raise PinCensusError(f"missing gate config {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise PinCensusError(f"{path} will not parse ({exc})") from exc
    cfg = loaded.get("pin_census")
    if not isinstance(cfg, dict):
        raise PinCensusError(f"{path} declares no `pin_census` block")
    return cfg


def _globs(cfg: dict, key: str, default: Sequence[str]) -> list[str]:
    value = cfg.get(key)
    return [str(v) for v in value] if isinstance(value, list) and value else list(default)


def scan_globs(cfg: dict) -> list[str]:
    """Every glob this census reads, for ``filter_scope``."""
    out: list[str] = []
    for key, default in (
        ("command_files", DEFAULT_COMMAND_GLOBS),
        ("action_files", DEFAULT_ACTION_GLOBS),
        ("compose_files", DEFAULT_COMPOSE_GLOBS),
    ):
        out += _globs(cfg, key, default)
    return sorted(set(out))


def _matches(rel: str, globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in globs)


def filter_scope(files: Iterable[str], cfg: Optional[dict] = None) -> list[str]:
    """The subset of *files* this census would read at all."""
    globs = scan_globs(cfg if cfg is not None else load_config())
    out: list[str] = []
    for entry in files or []:
        rel = str(entry).replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        if _matches(rel, globs):
            out.append(rel)
    return sorted(set(out))


def _excluded(rel: str, cfg: dict) -> bool:
    for entry in cfg.get("exclude") or []:
        if isinstance(entry, dict) and fnmatch.fnmatch(rel, str(entry.get("path", ""))):
            return True
    return False


def _expand(repo: Path, globs: Sequence[str]) -> list[str]:
    out: set[str] = set()
    for pattern in globs:
        for path in repo.glob(pattern):
            if path.is_file():
                out.add(path.relative_to(repo).as_posix())
    return sorted(out)


# ── collection ─────────────────────────────────────────────────────────────
def collect(
    repo: Path = REPO,
    cfg: Optional[dict] = None,
    only: Optional[Sequence[str]] = None,
) -> tuple[list[dict], list[str]]:
    """Every site, plus everything that could not be measured."""
    cfg = cfg if cfg is not None else load_config()
    digested, unmeasurable = vendored_repos(repo)

    command_globs = _globs(cfg, "command_files", DEFAULT_COMMAND_GLOBS)
    action_globs = _globs(cfg, "action_files", DEFAULT_ACTION_GLOBS)
    compose_globs = _globs(cfg, "compose_files", DEFAULT_COMPOSE_GLOBS)

    if only is None:
        command_files = _expand(repo, command_globs)
        action_files = _expand(repo, action_globs)
        compose_files = _expand(repo, compose_globs)
    else:
        wanted = filter_scope(only, cfg)
        command_files = [f for f in wanted if _matches(f, command_globs)]
        action_files = [f for f in wanted if _matches(f, action_globs)]
        compose_files = [f for f in wanted if _matches(f, compose_globs)]

    cache: dict[str, Optional[str]] = {}

    def read(rel: str) -> Optional[str]:
        if rel in cache:
            return cache[rel]
        try:
            cache[rel] = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unmeasurable.append(f"{rel}: unreadable ({exc})")
            cache[rel] = None
        return cache[rel]

    sites: list[dict] = []
    for rel in command_files:
        if _excluded(rel, cfg):
            continue
        text = read(rel)
        if text is not None:
            sites += scan_commands(rel, text)
    for rel in action_files:
        if _excluded(rel, cfg):
            continue
        text = read(rel)
        if text is not None:
            sites += scan_actions(rel, text)
    for rel in compose_files:
        if _excluded(rel, cfg):
            continue
        text = read(rel)
        if text is None:
            continue
        found, problems = scan_compose(rel, text, digested)
        sites += found
        unmeasurable += problems

    seen: set[str] = set()
    unique: list[dict] = []
    for site in sorted(sites, key=lambda s: (s["file"], s["kind"], s["subject"])):
        if site["key"] in seen:
            continue
        seen.add(site["key"])
        unique.append(site)
    return unique, unmeasurable


# ── the surveyed-not-gated half ────────────────────────────────────────────
_RANGE_OPS = ("~=", ">=", "<=", "!=", ">", "<")


def _rate(part: int, total: int) -> Optional[float]:
    """A percentage, or None when nothing was measured.

    None and never 0.0: an empty denominator is "nobody measured", and a 0.0
    beside it reads as a measured zero (args/perfect_score_gate.yaml, ratcheted
    to 0 by rem-hyg-13). 100.0 is reserved for a rate that IS 100 — an imperfect
    share FLOORS to 99.9 rather than rounding up into a perfect score.
    """
    if total <= 0:
        return None
    if part >= total:
        return 100.0
    return min(round(part * 100.0 / total, 1), 99.9)


def survey_requirements(repo: Path = REPO) -> dict:
    """``requirements.txt`` specifier shapes. REPORTED, never gated.

    The install-time pin for a deployment is the vendored wheel set, and gating
    45 ranges would refuse routine work. The number is quoted so a later card
    decides with evidence rather than by feel.
    """
    empty = {
        "measured": False, "declared": None, "pinned": None, "ranged": None,
        "direct_reference": None, "unspecified": None, "unpinned_pct": None,
        "examples": [],
    }
    path = repo / "requirements.txt"
    if not path.exists():
        return {**empty, "reason": "requirements.txt is absent"}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {**empty, "reason": f"requirements.txt unreadable ({exc})"}

    pinned = ranged = direct = unspecified = 0
    examples: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if requirement_is_pinned(line):
            pinned += 1
        elif _DIRECT_REFERENCE.match(line):
            # A git/URL reference whose ref is not a sha. NOT merged into
            # `ranged`: a version range is resolved by the index against a
            # published release, a mutable git tag by whoever owns the repo.
            direct += 1
            if len(examples) < 5:
                examples.append(line)
        elif any(op in line for op in _RANGE_OPS):
            ranged += 1
            if len(examples) < 5:
                examples.append(line)
        else:
            unspecified += 1
    declared = pinned + ranged + direct + unspecified
    return {
        "measured": True,
        "reason": "the install-time pin is the vendored wheel set "
                  "(tools/airgap/wheel_vendor.py); gating every range would refuse "
                  "routine work, so this is surveyed and not gated",
        "declared": declared,
        "pinned": pinned,
        "ranged": ranged,
        "direct_reference": direct,
        "unspecified": unspecified,
        "unpinned_pct": _rate(declared - pinned, declared),
        "examples": examples,
    }


# ── census file ────────────────────────────────────────────────────────────
def census_path(repo: Path, cfg: dict) -> Path:
    return repo / str(cfg.get("census_file", "args/pin_census.txt"))


def load_census(repo: Path, cfg: dict) -> tuple[set[str], list[str]]:
    """Registered keys, plus any entry whose reason is too short to be one."""
    path = census_path(repo, cfg)
    if not path.exists():
        return set(), []
    minimum = int(cfg.get("min_reason_chars", 12))
    entries: set[str] = set()
    thin: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        key, _, reason = raw.partition("#")
        key = key.strip()
        if not key:
            continue
        entries.add(key)
        if len(reason.strip()) < minimum:
            thin.append(key)
    return entries, thin


def build_report(
    repo: Path = REPO,
    only: Optional[Sequence[str]] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """The whole census as data. Raises ``PinCensusError`` if unproducible."""
    cfg = cfg if cfg is not None else load_config(repo if repo != REPO else None)
    census, thin = load_census(repo, cfg)
    sites, unmeasurable = collect(repo, cfg, only)
    keys = {s["key"] for s in sites}

    unregistered = [s for s in sites if s["key"] not in census]
    ceiling = int(cfg.get("pin_max", 0))
    partial = only is not None

    report = {
        "scope": "changed" if partial else "tree",
        "partial": partial,
        "scanned_files": sorted({s["file"] for s in sites}) if partial else None,
        "sites_seen": len(sites),
        "by_kind": {k: len([s for s in sites if s["kind"] == k]) for k in KINDS},
        "registered": len(keys & census),
        "unregistered": unregistered,
        "census_file": census_path(repo, cfg).relative_to(repo).as_posix(),
        "census_size": len(census),
        "ceiling": ceiling,
        "thin_reasons": sorted(thin),
        "unmeasurable": sorted(set(unmeasurable)),
        "surveyed_not_gated": {"requirements_txt": survey_requirements(repo)},
    }
    # A partial scan cannot tell a deleted site from an unscanned one, so the
    # ceiling and stale halves are suppressed on it — "225 entries are stale" on
    # a one-file commit is how a check earns itself a `|| true`.
    report["over_ceiling"] = (not partial) and len(census) > ceiling
    report["stale_entries"] = sorted(census - keys) if not partial else []
    report["ok"] = (
        not unregistered
        and not report["over_ceiling"]
        and not report["unmeasurable"]
        and not thin
    )
    return report


def prune(repo: Path = REPO, cfg: Optional[dict] = None) -> dict:
    """Drop census entries whose site no longer exists. Only ever SHRINKS."""
    cfg = cfg if cfg is not None else load_config(repo if repo != REPO else None)
    path = census_path(repo, cfg)
    if not path.exists():
        return {"dropped": 0, "kept": 0, "census_file": path.as_posix()}
    sites, unmeasurable = collect(repo, cfg, None)
    if unmeasurable:
        # Pruning against a partial read would DELETE a live entry, which is the
        # one direction that loses information.
        raise PinCensusError(
            "refusing to prune against an unmeasurable scan: "
            + "; ".join(sorted(set(unmeasurable)))
        )
    live = {s["key"] for s in sites}
    kept_lines: list[str] = []
    dropped = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        bare = raw.partition("#")[0].strip()
        if bare and bare not in live:
            dropped += 1
            continue
        kept_lines.append(raw)
    path.write_text(
        "\n".join(kept_lines).rstrip("\n") + "\n", encoding="utf-8", newline="\n"
    )
    return {"dropped": dropped, "kept": len(live), "census_file": path.as_posix()}


#: One reason per KIND, each naming the repair rather than restating the finding.
SEED_REASONS = {
    KIND_INSTALL: "unpinned at adoption -- pin it with `==<version>`",
    KIND_SCRIPT: "remote install script, unpinned by construction -- vendor the "
                 "installer, or fetch a release asset and check its sha256",
    KIND_ACTION: "tag-pinned at adoption -- repin to the 40-hex commit sha",
    KIND_IMAGE: "tag-pinned at adoption -- measure the digest and record it in "
                "vendor/images/",
}


def seed(repo: Path = REPO, cfg: Optional[dict] = None) -> dict:
    """The adoption census for today's tree, as lines. Writes NOTHING."""
    cfg = cfg if cfg is not None else load_config(repo if repo != REPO else None)
    sites, unmeasurable = collect(repo, cfg, None)
    if unmeasurable:
        raise PinCensusError(
            "refusing to seed against an unmeasurable scan: "
            + "; ".join(sorted(set(unmeasurable)))
        )
    return {
        "sites": len(sites),
        "lines": [
            f"{s['key']}  # {s['reference']}: {SEED_REASONS[s['kind']]}"
            for s in sites
        ],
    }


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


# ── CLI ────────────────────────────────────────────────────────────────────
def _print(report: dict) -> None:
    survey = report["surveyed_not_gated"]["requirements_txt"]
    kinds = report["by_kind"]
    print(
        f"Pin census ({report['scope']}): {report['sites_seen']} site(s) seen, "
        f"{report['registered']} registered, {len(report['unregistered'])} unregistered "
        f"| census {report['census_size']} (ceiling {report['ceiling']})"
    )
    print(
        f"  by kind: {kinds[KIND_INSTALL]} unpinned install, "
        f"{kinds[KIND_ACTION]} tag-pinned action, "
        f"{kinds[KIND_IMAGE]} undigested image, "
        f"{kinds[KIND_SCRIPT]} unpinned script"
    )
    for site in report["unregistered"][:40]:
        print(f"  NEW  {site['file']}:{site['line']}  [{site['kind']}] {site['reference']}")
    for entry in report["stale_entries"][:20]:
        print(f"  STALE  {entry}")
    for entry in report["thin_reasons"][:20]:
        print(f"  NO REASON  {entry}")
    for entry in report["unmeasurable"]:
        print(f"  UNMEASURABLE  {entry}")
    if report["over_ceiling"]:
        print(
            f"  CEILING BREACHED: census {report['census_size']} > "
            f"{report['ceiling']}. pin_max may only go DOWN."
        )
    if survey["measured"]:
        print(
            f"  surveyed, NOT gated: requirements.txt declares {survey['declared']} "
            f"distribution(s) -- {survey['pinned']} pinned, {survey['ranged']} ranged, "
            f"{survey['direct_reference']} direct reference(s), "
            f"{survey['unspecified']} unspecified; {survey['unpinned_pct']}% not pinned. "
            f"{survey['reason']}."
        )
    else:
        print(f"  surveyed, NOT gated: UNMEASURABLE -- {survey['reason']}")


_REPAIR = (
    "\nA reference that does not name the bytes it resolves to lets somebody else "
    "decide what this build runs. Fix it at the site:\n"
    "  * pip / npm -g  ->  `pkg==<version>`\n"
    "  * uses:         ->  `owner/repo@<40-hex commit sha>` (keep the tag in a "
    "trailing comment)\n"
    "  * compose       ->  measure the digest, record `repo@sha256:...` in "
    "vendor/images/, then `python tools/airgap/image_vendor.py --verify --topic <t>`\n"
    "  * curl | sh     ->  vendor the installer, or fetch a release asset and check "
    "its sha256\n"
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().split("\n")[0]
    )
    parser.add_argument("--check", action="store_true", help="exit 1 on a NEW site")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--changed", nargs="*", help="limit the scan to these files")
    parser.add_argument("--staged", action="store_true", help="scan only staged files")
    parser.add_argument(
        "--prune", action="store_true",
        help="drop census entries whose site is gone; only ever SHRINKS",
    )
    parser.add_argument(
        "--seed", action="store_true",
        help="print the adoption census for today's tree; writes nothing",
    )
    parser.add_argument(
        "--root", default=None,
        help="checkout to scan (default: the one this tool lives in)",
    )
    args = parser.parse_args(argv)
    repo = Path(args.root).resolve() if args.root else REPO

    try:
        cfg = load_config(repo if repo != REPO else None)
        if args.prune:
            result = prune(repo, cfg)
            print(f"Pin census: pruned {result['dropped']} stale entr(ies).")
            return 0
        if args.seed:
            print("\n".join(seed(repo, cfg)["lines"]))
            return 0

        only: Optional[list[str]] = None
        if args.staged:
            only = _staged_files(repo)
        elif args.changed is not None:
            only = list(args.changed)

        report = build_report(repo, only, cfg)
    except PinCensusError as exc:
        # Exit 2: the census could not be PRODUCED, which is never the same as a
        # census that found nothing.
        print(f"pin_census: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print(report)

    if args.check and not report["ok"]:
        print(
            _REPAIR
            + f"Registering it in {report['census_file']} is a debt you have "
              "written down, and it breaches the ceiling.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    os.chdir(REPO)
    raise SystemExit(main())
