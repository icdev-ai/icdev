# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Claim parser (Mode B input).

Where ``capability_extractor`` answers *"what can this code actually do?"* (the
**exercised** capability manifest, derived from the AST), this module answers the
complementary question *"what does the author **claim** it does?"* — the
**declared** capability set, parsed from natural-language prose.

The two feed SIPA's intent reconciliation:

  * **Mode A (provenance-aware)** reconciles exercised capabilities against a
    formal RTM / PRD.
  * **Mode B (provenance-blind / claim-based)** has no RTM — only the author's
    stated purpose. It reconciles exercised capabilities against this *claimed*
    set. **The absence of a claim for a capability the code exercises is the
    signal Mode B fires on** (an *undisclosed_capability* finding): a module whose
    README says "formats JSON" but which opens a socket has exercised a behavior
    it never disclosed.

Sources of a claim (in precedence-neutral union):

  * ``README.*`` files found under the target (any extension — md/rst/txt/none).
  * Module / package **docstrings** (the top-of-file triple-quoted string of
    every ``.py`` file — read via ``ast`` only, never importing/executing it).
  * A caller-supplied ``declared_purpose`` free-text string (e.g. a marketplace
    listing blurb or a ticket description).

Mapping is a **deterministic keyword/rule lexicon** — never an LLM call — so the
declared set is reproducible and auditable. Each phrase compiles to a
word-boundary regex (whitespace-flexible, tolerant of common verb/plural
suffixes) keyed to one of ``constants.CAPABILITY_TYPES``. A natural-language
claim contributes a capability iff one of that capability's phrases matches; the
matched phrase is retained as evidence for the HITL reviewer.

Lexicon (illustrative, not exhaustive — see ``_LEXICON`` below):
  * download / fetch / upload / API / HTTP(S) / URL / request / endpoint /
    webhook / socket / network          -> ``network_egress``
  * save / write / cache / log to file / to disk / directory / persist
                                         -> ``filesystem``
  * run/spawn/shell command / subprocess / shell out / system command
                                         -> ``process_exec``
  * eval / dynamic code / code generation / runtime import / plugin
                                         -> ``dynamic_code``
  * encrypt / decrypt / sign / signature / hash / cipher / TLS / SSL / HMAC
                                         -> ``crypto``
  * environment variable / secret / credential / API key / token / password
                                         -> ``env_secret``
  * pickle / unpickle / marshal / yaml.load        -> ``serialization``
  * base64 / obfuscate / hex-encoded / minified     -> ``obfuscation``

Pure-stdlib + ``constants``: no DB, no network, no subprocess. Read-only.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Optional

from tools.integrity.constants import CAPABILITY_TYPES

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.claim_parser")

# Directories never worth walking (mirror capability_extractor's exclusions).
_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp", ".mypy_cache"}
_MAX_FILE_BYTES = 5_000_000  # skip files larger than 5 MB (generated/vendored blobs)

# README basenames are matched case-insensitively against this stem.
_README_STEM = re.compile(r"^readme(\.|$)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Lexicon — natural-language phrase -> capability_type
# --------------------------------------------------------------------------- #
# Each value is a list of *phrases*. A phrase is a lowercase token or short
# multi-word string. Phrases are compiled to a word-boundary regex that is
# tolerant of internal whitespace runs and common verb/plural suffixes
# (s / es / ed / ing / d), so "upload" matches "uploads"/"uploaded"/"uploading"
# and "log to file" matches "logs to file"/"log to files" loosely. Irregular
# forms that the suffix rule cannot reach (e.g. "wrote", "written") are listed
# explicitly.
#
# Keys MUST be members of constants.CAPABILITY_TYPES (asserted at import time).
_LEXICON: dict[str, list[str]] = {
    "network_egress": [
        "download", "upload", "fetch", "http", "https", "url", "uri",
        "api", "rest api", "graphql", "grpc", "endpoint", "webhook",
        "request", "send to", "post to", "get from", "pull from", "push to",
        "socket", "network", "internet", "remote server", "web service",
        "dns", "call the api", "calls an api", "calls out to", "phone home",
        "telemetry", "report back", "transmit", "exfiltrate", "beacon",
        "email", "e-mail", "smtp", "send mail", "mail server", "notify by email",
    ],
    "filesystem": [
        "save", "write", "wrote", "written", "cache", "persist", "store",
        "stored", "log to file", "to a file", "to file", "to disk", "on disk",
        "file system", "filesystem", "directory", "folder", "create a file",
        "delete a file", "read a file", "read from file", "output file",
        "temp file", "temporary file", "scratch file", "dump to",
    ],
    "process_exec": [
        "run command", "runs command", "run a command", "shell command",
        "shell out", "spawn", "subprocess", "system command", "os command",
        "execute a command", "executes command", "invoke command",
        "launch process", "child process", "run a binary", "run a script",
        "command line tool", "shells out", "fork", "exec",
    ],
    "dynamic_code": [
        "eval", "dynamic code", "code generation", "generate code",
        "runtime import", "import at runtime", "compile code", "exec code",
        "execute python", "plugin", "load module dynamically", "monkeypatch",
        "metaprogramming",
    ],
    "crypto": [
        "encrypt", "decrypt", "sign", "signature", "hash", "hashing",
        "cipher", "encryption", "cryptography", "cryptographic", "hmac",
        "tls", "ssl", "certificate", "digest", "checksum", "keypair",
        "public key", "private key", "aes", "rsa",
    ],
    "env_secret": [
        "environment variable", "env var", "secret", "credential",
        "api key", "access key", "token", "password", "passphrase",
        "private key", "vault", "keyring", "auth token", "bearer token",
    ],
    "serialization": [
        "pickle", "unpickle", "marshal", "yaml.load", "yaml load",
        "load yaml", "shelve", "dill", "deserialize untrusted",
    ],
    "obfuscation": [
        "base64", "base 64", "obfuscate", "obfuscation", "obfuscated",
        "hex-encoded", "hex encoded", "minified", "packed", "encoded payload",
        "xor encode", "rot13",
    ],
}

# Validate the lexicon keys against the canonical taxonomy at import time — a
# typo here would silently drop a whole capability from Mode B's claim set.
assert set(_LEXICON) <= set(CAPABILITY_TYPES), (
    f"claim_parser lexicon keys not in CAPABILITY_TYPES: "
    f"{set(_LEXICON) - set(CAPABILITY_TYPES)}"
)


def _compile(phrase: str) -> re.Pattern:
    """Compile one lexicon phrase to a whitespace/suffix-tolerant word regex."""
    # Escape each token, join on flexible whitespace, allow common suffixes on
    # the final token, and anchor on word boundaries so "api" does not match
    # "rapid" and "fetch" does not match "prefetcher".
    tokens = phrase.split()
    body = r"\s+".join(re.escape(t) for t in tokens)
    return re.compile(r"\b" + body + r"(?:s|es|ed|ing|d)?\b", re.IGNORECASE)


# phrase-string -> (capability_type, compiled regex)
_COMPILED: list[tuple[str, str, re.Pattern]] = [
    (cap, phrase, _compile(phrase))
    for cap, phrases in _LEXICON.items()
    for phrase in phrases
]


# --------------------------------------------------------------------------- #
# Core text -> claim mapping
# --------------------------------------------------------------------------- #
def map_text(text: str) -> dict[str, list[str]]:
    """Map a blob of natural-language text to ``{capability_type: [phrases]}``.

    Returns only the capabilities the text *claims*; an absent key means the text
    made no claim for that capability. Matched phrases are deduped + sorted so the
    evidence is stable across runs.
    """
    if not text:
        return {}
    matched: dict[str, set[str]] = {}
    for cap, phrase, pattern in _COMPILED:
        if pattern.search(text):
            matched.setdefault(cap, set()).add(phrase)
    return {cap: sorted(hits) for cap, hits in matched.items()}


# --------------------------------------------------------------------------- #
# Source discovery
# --------------------------------------------------------------------------- #
def _read_text(file_path: Path) -> Optional[str]:
    """Read a text file defensively; ``None`` on unreadable/oversize."""
    try:
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        logger.warning("claim_parser: cannot read %s: %s", file_path, exc)
        return None


def _module_docstring(file_path: Path) -> Optional[str]:
    """Return a ``.py`` file's module-level docstring (``ast`` only — never run)."""
    src = _read_text(file_path)
    if src is None:
        return None
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError as exc:
        logger.warning("claim_parser: cannot parse %s: %s", file_path, exc)
        return None
    return ast.get_docstring(tree)


def _rel(path: Path, root: Path) -> str:
    """Path relative to the scan root (portable); original string on cross-drive."""
    try:
        return os.path.relpath(path, root)
    except (ValueError, OSError):
        return str(path)


def _iter_files(root: Path):
    """Yield (kind, file_path) for README.* and ``.py`` files under ``root``.

    ``kind`` is ``"readme"`` or ``"docstring"``. Vendored/cache dirs are skipped.
    """
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            fp = Path(dirpath) / fn
            if _README_STEM.match(fn):
                yield "readme", fp
            elif fn.endswith((".py", ".pyw")):
                yield "docstring", fp


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse_claim(
    path: str | os.PathLike,
    declared_purpose: Optional[str] = None,
) -> dict:
    """Parse the *claimed* capability set for a file or directory tree.

    Reads ``README.*`` files, ``.py`` module/package docstrings, and an optional
    caller-supplied ``declared_purpose`` string, then maps each through the
    deterministic lexicon to ``constants.CAPABILITY_TYPES``.

    Args:
        path: a directory to walk, a single file (``.py`` -> its docstring; any
            other file -> read as prose), or a README path.
        declared_purpose: free-text purpose claim (marketplace blurb, ticket,
            PRD summary). Mapped exactly like a README.

    Returns:
        ``{"claimed_capabilities": set[str], "sources": [...]}`` where each
        source is ``{"kind", "ref", "capabilities": [...], "matched": {cap:[phrases]}}``.
        ``claimed_capabilities`` is the union across all sources. A capability
        *absent* from that set is what Mode B flags when the code exercises it —
        see :func:`undisclosed_capabilities`.
    """
    root = Path(path)
    sources: list[dict] = []
    claimed: set[str] = set()

    def _record(kind: str, ref: str, text: Optional[str]) -> None:
        if not text:
            return
        matched = map_text(text)
        # A source is retained even with zero matches: "this README claims
        # nothing" is itself evidence Mode B can cite.
        caps = sorted(matched)
        sources.append(
            {"kind": kind, "ref": ref, "capabilities": caps, "matched": matched}
        )
        claimed.update(caps)

    if root.is_dir():
        for kind, fp in _iter_files(root):
            ref = _rel(fp, root)
            text = _module_docstring(fp) if kind == "docstring" else _read_text(fp)
            _record(kind, ref, text)
    elif root.is_file():
        if root.suffix in (".py", ".pyw"):
            _record("docstring", root.name, _module_docstring(root))
        else:
            _record("readme", root.name, _read_text(root))
    else:
        logger.warning("claim_parser: path does not exist: %s", root)

    if declared_purpose:
        _record("declared_purpose", "<declared_purpose>", declared_purpose)

    return {"claimed_capabilities": claimed, "sources": sources}


def undisclosed_capabilities(
    claim: dict,
    exercised: set[str] | list[str],
) -> set[str]:
    """Capabilities the code *exercises* but never *claims* — the Mode B signal.

    Args:
        claim: a :func:`parse_claim` result (or any dict with
            ``claimed_capabilities``).
        exercised: the capability types actually found by
            ``capability_extractor`` (e.g. ``{r["capability_type"] for r in
            extract(path)}``).

    Returns:
        ``set(exercised) - claimed`` — every capability exercised without a
        disclosing claim. An empty set means the disclosure fully covers behavior.
    """
    claimed = set(claim.get("claimed_capabilities", set()))
    return set(exercised) - claimed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="SIPA claim parser — README/docstrings/declared-purpose -> "
        "claimed capability set (Mode B disclosure baseline).",
    )
    parser.add_argument("path", help="file or directory to parse claims from")
    parser.add_argument(
        "--declared-purpose",
        default=None,
        help="free-text purpose claim to fold into the claimed set",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    result = parse_claim(args.path, declared_purpose=args.declared_purpose)
    if args.json:
        out = {
            "claimed_capabilities": sorted(result["claimed_capabilities"]),
            "sources": result["sources"],
        }
        print(json.dumps(out, indent=2))
    else:
        caps = sorted(result["claimed_capabilities"])
        print(f"Claimed capabilities ({len(caps)}): {', '.join(caps) or '(none)'}")
        for src in result["sources"]:
            tag = ", ".join(src["capabilities"]) or "(no claim)"
            print(f"  [{src['kind']}] {src['ref']}: {tag}")


if __name__ == "__main__":
    main()
