# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Capability extractor (Phase 1).

Turns the boolean "is this script safe?" AST scan from
``tools/marketplace/openclaw_bridge.analyze_script_safety`` into a **normalized
behavioral capability manifest**: instead of a yes/no verdict, every interesting
call site becomes a structured record describing *what the code can do* and the
*literal it does it to*. That manifest is the input to SIPA's downstream intent
reconciliation (capability-vs-RTM in Mode A, capability-vs-claim in Mode B).

Phase 1 detectors (Python ``ast`` only — never imports or executes the target):

  * ``network_egress`` — ``socket`` (socket/create_connection/create_server),
    ``http.client`` (HTTP[S]Connection), ``urllib.request`` (urlopen/Request/
    urlretrieve), ``requests`` and ``httpx`` HTTP verbs. Evidence captures the
    target host / URL literal when it is a constant.
  * ``filesystem`` — builtin ``open``/``io.open`` (with mode), pathlib ``Path``
    read/write methods (``read_text``/``write_text``/``read_bytes``/
    ``write_bytes``/``touch``), ``shutil`` copy/move/tree ops, and mutating
    ``os.*`` file ops (remove/rename/mkdir/...). Evidence captures the path
    literal + access mode.
  * ``process_exec`` — ``subprocess.*`` (run/call/Popen/...), ``os.system``/
    ``os.popen``/``os.exec*``/``os.spawn*``/``os.posix_spawn*``,
    ``multiprocessing`` Process/Pool, ``pty.spawn``. Evidence captures the
    command literal + whether ``shell=True``.
  * ``dynamic_code`` — ``eval``/``exec``/``compile``/``__import__`` builtins,
    ``importlib.import_module``, ``types.FunctionType``/``CodeType``. Evidence
    captures the code literal and an ``obfuscated_input`` flag when the argument
    is a decode/decompress call (the decode-then-exec backdoor shape).
  * ``crypto`` — ``hashlib`` hash constructors (weak ones — md5/sha1 — flagged
    ``weak``), ``hmac``, ``ssl`` context tweaks (``_create_unverified_context`` /
    ``check_hostname=False`` flagged ``insecure``), and ``Crypto``/``Cryptodome``/
    ``cryptography``/``nacl`` usage.
  * ``env_secret`` — ``os.getenv``/``os.environ.get``/``os.environ[...]``,
    ``keyring.*``, ``dotenv.*``, and *reads* of secret-looking paths
    (``.env``/credentials/keys/...). Evidence captures the key / path literal.
  * ``serialization`` — ``pickle``/``marshal``/``dill`` load+dump, ``shelve.open``,
    and ``yaml.load``/``unsafe_load``/``full_load`` (``safe_loader`` flag).
    ``deserialize`` marks the untrusted-bytes-to-object direction.
  * ``obfuscation`` — ``base64``/``codecs``/``zlib``/``binascii`` decode calls,
    ``bytes.fromhex``, long base64/hex string|bytes literals, and char-code
    assembly (``bytes([...])`` of many int literals).

Each record is ``{file_path, function_name, capability_type, evidence, line_start,
line_end, risk_weight}`` and is persisted **append-only** to
``integrity_capabilities`` via the same RLS-aware path (``get_connection`` +
``tenant_id``/``classification`` stamping) the scanner adapters use, so the
capability writer and the finding writer can never drift.

Import aliasing is resolved (``import requests as rq`` -> ``rq.get`` is recognized
as ``requests.get``; ``from urllib.request import urlopen`` -> a bare ``urlopen``
is recognized as ``urllib.request.urlopen``) so renaming an import cannot hide a
capability. The manifest now covers all eight ``CAPABILITY_TYPES``; remaining
phases extend coverage to non-Python languages via Semgrep rules using the same
taxonomy.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from tools.integrity.constants import RISK_WEIGHTS_CAPABILITY
from tools.integrity.db.init_db import init_db

# Reuse ingest's context/backend helpers so the capability INSERT and the
# tenant/classification stamping match the finding writer exactly.
from tools.integrity.ingest import _backend_of, _caller_context

logger = logging.getLogger("icdev.integrity.capability_extractor")

# Directories never worth walking when a directory tree is scanned.
_EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp", ".mypy_cache"}
_MAX_FILE_BYTES = 5_000_000  # skip files larger than 5 MB (generated/vendored blobs)


# --------------------------------------------------------------------------- #
# Detector tables — canonical (alias-resolved) call name -> capability_type
# --------------------------------------------------------------------------- #
# Exact canonical names that are network egress on their own.
_NETWORK_EXACT = {
    "socket.socket",
    "socket.create_connection",
    "socket.create_server",
    "urllib.request.urlopen",
    "urllib.request.Request",
    "urllib.request.urlretrieve",
    "http.client.HTTPConnection",
    "http.client.HTTPSConnection",
}
# Modules whose HTTP-verb / client attrs are network egress (requests/httpx).
_NETWORK_HTTP_MODULES = {"requests", "httpx"}
_NETWORK_HTTP_ATTRS = {
    "get", "post", "put", "delete", "patch", "head", "options", "request",
    "Session", "Client", "AsyncClient", "stream",
}

# Builtin / io open.
_FS_OPEN = {"open", "io.open"}
# pathlib.Path methods that are unambiguously a filesystem read/write. Generic
# ``.write``/``.read`` are intentionally excluded — they collide with sockets,
# StringIO, etc. and would flood the manifest with false positives.
_FS_PATH_READ = {"read_text", "read_bytes"}
_FS_PATH_WRITE = {"write_text", "write_bytes", "touch"}
_FS_PATH_METHODS = _FS_PATH_READ | _FS_PATH_WRITE
# shutil filesystem operations (all mutate or copy bytes on disk).
_FS_SHUTIL_ATTRS = {
    "copy", "copy2", "copyfile", "copyfileobj", "copytree", "copymode",
    "copystat", "move", "rmtree", "make_archive", "unpack_archive",
}
# Mutating os.* file operations (path-taking; all write/destroy on disk).
_FS_OS_EXACT = {
    "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.mkdir",
    "os.makedirs", "os.rename", "os.renames", "os.replace", "os.truncate",
    "os.chmod", "os.chown", "os.link", "os.symlink",
}

# subprocess attrs that spawn a child process.
_PROC_SUBPROCESS_ATTRS = {
    "run", "call", "check_call", "check_output", "Popen",
    "getoutput", "getstatusoutput",
}
# Exact os.* process-spawning names not covered by the os.exec*/os.spawn* prefixes.
_PROC_OS_EXACT = {"os.system", "os.popen", "os.posix_spawn", "os.posix_spawnp"}
_PROC_MULTIPROCESSING = {"multiprocessing.Process", "multiprocessing.Pool"}

# --- dynamic_code ---------------------------------------------------------- #
# Bare builtins that turn data into executable code at runtime.
_DYNCODE_BUILTINS = {"eval", "exec", "compile", "__import__"}
# Canonical dotted names that build code / functions / do runtime imports.
_DYNCODE_EXACT = {
    "importlib.import_module", "importlib.__import__", "importlib.reload",
    "types.FunctionType", "types.CodeType", "types.LambdaType",
}

# --- crypto ---------------------------------------------------------------- #
# hashlib hash constructors (a crypto capability; weak ones are flagged below).
_CRYPTO_HASHLIB = {
    "md5", "sha1", "sha224", "sha256", "sha384", "sha512",
    "sha3_224", "sha3_256", "sha3_384", "sha3_512", "shake_128", "shake_256",
    "blake2b", "blake2s", "new", "pbkdf2_hmac", "scrypt",
}
# Algorithms considered cryptographically weak / broken.
_CRYPTO_WEAK_HASH = {"md5", "sha1", "md4", "sha"}
# ssl context constructors / tweaks (TLS posture is a crypto-relevant capability).
_CRYPTO_SSL_EXACT = {
    "ssl._create_unverified_context", "ssl.create_default_context",
    "ssl.SSLContext", "ssl.wrap_socket",
}
# ssl calls that are insecure on their own (verification disabled).
_CRYPTO_SSL_INSECURE = {"ssl._create_unverified_context"}
# Third-party crypto libraries — any call whose head resolves to one of these.
_CRYPTO_LIB_HEADS = {"Crypto", "Cryptodome", "cryptography", "nacl"}

# --- env_secret ------------------------------------------------------------ #
# Exact dotted names that read environment / secret material.
_ENVSECRET_EXACT = {"os.getenv", "os.environ.get", "os.putenv"}
# Modules whose calls are all secret-material access (keyring, python-dotenv).
_ENVSECRET_HEADS = {"keyring", "dotenv"}
# os.environ resolved name (used by the subscript visitor: os.environ['SECRET']).
_ENVSECRET_MAPPING = {"os.environ"}
# Substrings in a filesystem path literal that mark it as secret material; a read
# of such a path is flagged env_secret *in addition to* filesystem.
_SECRET_PATH_MARKERS = (
    ".env", "credential", "secret", "id_rsa", "id_dsa", "id_ecdsa", ".pem",
    ".key", "/keys/", "\\keys\\", "password", ".aws/credentials", ".npmrc",
    ".pgpass", ".netrc", "token", ".ssh/", "private_key",
)

# --- serialization --------------------------------------------------------- #
# Exact dotted (de)serialization names. ``*.load``/``*.loads``/``Unpickler``/
# ``shelve.open`` are the dangerous *deserialization* side (untrusted bytes ->
# objects / code); dump/dumps are included for capability completeness.
_SERIAL_EXACT = {
    "pickle.load", "pickle.loads", "pickle.dump", "pickle.dumps",
    "pickle.Unpickler", "pickle.Pickler",
    "marshal.load", "marshal.loads", "marshal.dump", "marshal.dumps",
    "shelve.open",
    "dill.load", "dill.loads", "dill.dump", "dill.dumps",
}
# Serialization tails that *deserialize* (the high-risk direction).
_SERIAL_DESERIALIZE_TAILS = {"load", "loads", "Unpickler", "open"}
# yaml loaders that are unsafe (construct arbitrary Python). ``safe_load`` is OK.
_SERIAL_YAML_UNSAFE = {"load", "unsafe_load", "full_load"}

# --- obfuscation ----------------------------------------------------------- #
# Decode / decompress calls that turn opaque bytes back into code or data.
_OBFUSCATION_DECODE = {
    "base64.b64decode", "base64.b16decode", "base64.b32decode",
    "base64.b85decode", "base64.a85decode", "base64.decodebytes",
    "base64.urlsafe_b64decode", "base64.standard_b64decode",
    "codecs.decode", "codecs.encode",
    "zlib.decompress", "gzip.decompress", "bz2.decompress", "lzma.decompress",
    "binascii.unhexlify", "binascii.a2b_base64", "binascii.a2b_hex",
    "binascii.a2b_qp",
}
# Attribute-only obfuscation tails matched even when the receiver is not a plain
# name chain (e.g. ``b'...'.fromhex(...)`` / ``bytes.fromhex(...)``).
_OBFUSCATION_ATTRS = {"fromhex"}
# Minimum int-literal count for a bytes([...])/bytearray([...]) to read as
# char-code assembly (a classic shellcode / hidden-string construction).
_CHAR_ASSEMBLY_MIN = 8
# Length / charset thresholds for a string|bytes literal to read as a packed blob.
_OBFUSCATION_LITERAL_MIN = 64


# --------------------------------------------------------------------------- #
# AST helpers
# --------------------------------------------------------------------------- #
def _literal(node: Optional[ast.AST]) -> Any:
    """Return a constant's Python value, else ``None`` (f-strings/names/etc.)."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _dotted(func: ast.AST) -> Optional[str]:
    """Flatten an attribute chain (``a.b.c``) to a dotted string, else ``None``.

    Returns ``None`` for calls whose receiver is not a plain name chain (e.g. a
    call result ``foo().bar`` or a subscript ``d['k'].bar``) — those cannot be
    resolved to a canonical API without data-flow analysis.
    """
    parts: list[str] = []
    cur: ast.AST = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _canonical_name(func: ast.AST, aliases: dict[str, str]) -> Optional[str]:
    """Resolve a call's dotted name through the import-alias map.

    Only the *first* segment is rewritten (``rq.get`` -> ``requests.get`` when
    ``import requests as rq``; bare ``urlopen`` -> ``urllib.request.urlopen`` when
    ``from urllib.request import urlopen``), so deeper attribute access is
    preserved verbatim.
    """
    dotted = _dotted(func)
    if not dotted:
        return None
    segs = dotted.split(".")
    first = aliases.get(segs[0])
    if first is None:
        return dotted
    rest = segs[1:]
    return ".".join([first, *rest]) if rest else first


def _build_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local import bindings to their canonical module/attribute path.

    ``import socket`` -> {socket: socket}; ``import requests as rq`` ->
    {rq: requests}; ``import urllib.request`` -> {urllib: urllib} (the top
    package is what gets bound); ``from urllib.request import urlopen as uo`` ->
    {uo: urllib.request.urlopen}.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    # `import a.b.c` binds the top package `a`; canonical is `a`.
                    top = a.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — cannot resolve to a stdlib path
                continue
            module = node.module or ""
            for a in node.names:
                local = a.asname or a.name
                aliases[local] = f"{module}.{a.name}" if module else a.name
    return aliases


# --------------------------------------------------------------------------- #
# Per-capability evidence extraction
# --------------------------------------------------------------------------- #
def _first_arg(node: ast.Call) -> Optional[ast.AST]:
    return node.args[0] if node.args else None


def _network_evidence(name: str, node: ast.Call) -> dict:
    """Capture the target host / URL literal for a network egress call."""
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["literal"] = lit
        if "://" in lit:
            ev["url"] = lit
        else:
            ev["host"] = lit
    elif isinstance(arg, (ast.Tuple, ast.List)) and arg.elts:
        # socket.create_connection((host, port)) / socket.create_server(addr).
        host = _literal(arg.elts[0])
        if isinstance(host, str):
            ev["host"] = host
            ev["literal"] = host
    for kw in node.keywords:
        if kw.arg in ("url", "host"):
            v = _literal(kw.value)
            if isinstance(v, str):
                ev[kw.arg] = v
                ev.setdefault("literal", v)
    return ev


def _filesystem_evidence(name: str, node: ast.Call, path_attr: Optional[str]) -> dict:
    """Capture the path literal + access mode for a filesystem call.

    ``path_attr`` is the pathlib method name when the call is a ``Path`` method
    (its mode is implied by the method); otherwise mode comes from ``open``'s
    second argument, or defaults to ``w`` for the mutating shutil/os ops.
    """
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["path"] = lit
        ev["literal"] = lit
    # second positional (often a dst path for shutil.copy/move) is useful context.
    if len(node.args) >= 2:
        second = _literal(node.args[1])
        if isinstance(second, str):
            ev["dest"] = second

    if path_attr in _FS_PATH_WRITE:
        ev["mode"] = "w"
    elif path_attr in _FS_PATH_READ:
        ev["mode"] = "r"
    elif name in _FS_OPEN:
        ev["mode"] = _open_mode(node)
    else:
        # shutil.* / os.* mutating ops.
        ev["mode"] = "w"
    return ev


def _open_mode(node: ast.Call) -> str:
    """Resolve the access mode of an ``open()`` call (positional or ``mode=``)."""
    if len(node.args) >= 2:
        m = _literal(node.args[1])
        if isinstance(m, str):
            return m
    for kw in node.keywords:
        if kw.arg == "mode":
            m = _literal(kw.value)
            if isinstance(m, str):
                return m
    return "r"


def _process_evidence(name: str, node: ast.Call) -> dict:
    """Capture the command literal + shell flag for a process-spawn call."""
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["command"] = lit
        ev["literal"] = lit
    elif isinstance(arg, (ast.List, ast.Tuple)):
        parts = [_literal(e) for e in arg.elts]
        strs = [p for p in parts if isinstance(p, str)]
        if strs:
            ev["command"] = " ".join(strs)
            ev["literal"] = strs
    for kw in node.keywords:
        if kw.arg == "shell" and _literal(kw.value) is True:
            ev["shell"] = True
    return ev


# Regexes for "this string literal is a packed blob, not prose" (obfuscation).
_RE_BASE64 = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")
_RE_HEX = re.compile(r"^[0-9A-Fa-f]+$")


def _dynamic_code_evidence(name: str, node: ast.Call, aliases: dict[str, str]) -> dict:
    """Capture the code literal + whether the input is itself obfuscated."""
    ev: dict[str, Any] = {"api": name}
    arg = _first_arg(node)
    lit = _literal(arg)
    if isinstance(lit, str):
        ev["code"] = lit if len(lit) <= 200 else lit[:200] + "…"
        ev["literal"] = ev["code"]
    # eval(base64.b64decode(...)) / exec(codecs.decode(...)) — the decode-then-run
    # pattern is the strongest backdoor signal, so flag it on the dynamic record.
    if _contains_obfuscation_call(node, aliases):
        ev["obfuscated_input"] = True
    return ev


def _crypto_evidence(name: str, node: ast.Call) -> dict:
    """Capture the algorithm (+weak flag) for hashlib, or insecure TLS posture."""
    ev: dict[str, Any] = {"api": name}
    head = name.split(".")[0]
    tail = name.rsplit(".", 1)[-1]
    if head == "hashlib":
        algo = _literal(_first_arg(node)) if tail == "new" else tail
        if isinstance(algo, str):
            ev["algorithm"] = algo
            if algo.lower() in _CRYPTO_WEAK_HASH:
                ev["weak"] = True
    elif head == "ssl":
        if name in _CRYPTO_SSL_INSECURE:
            ev["insecure"] = True
        for kw in node.keywords:
            if kw.arg == "check_hostname" and _literal(kw.value) is False:
                ev["insecure"] = True
    return ev


def _env_secret_evidence(name: str, node: ast.Call) -> dict:
    """Capture the env-var / secret key name for an environment/secret read."""
    ev: dict[str, Any] = {"api": name}
    key = _literal(_first_arg(node))
    if isinstance(key, str):
        ev["key"] = key
        ev["literal"] = key
    return ev


def _serialization_evidence(name: str, node: ast.Call) -> dict:
    """Capture (de)serialization direction + yaml loader safety."""
    ev: dict[str, Any] = {"api": name}
    tail = name.rsplit(".", 1)[-1]
    head = name.split(".")[0]
    if tail in _SERIAL_DESERIALIZE_TAILS:
        ev["deserialize"] = True
    if head == "yaml":
        # yaml.load(...) is only safe with an explicit Safe/CSafe Loader.
        safe = False
        for kw in node.keywords:
            if kw.arg == "Loader":
                loader = _dotted(kw.value) or ""
                safe = "Safe" in loader
        ev["safe_loader"] = safe
        ev["deserialize"] = True
    return ev


def _looks_packed(value: Any) -> Optional[str]:
    """Return ``base64``/``hex`` if a literal reads as a packed blob, else None."""
    if isinstance(value, (bytes, bytearray)):
        try:
            value = value.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if len(stripped) < _OBFUSCATION_LITERAL_MIN:
        return None
    if _RE_HEX.fullmatch(stripped) and len(stripped) % 2 == 0:
        return "hex"
    if _RE_BASE64.fullmatch(stripped):
        # Require encoded-looking content (digits, or mixed case) so long prose
        # words / wrapped identifiers don't trip the detector.
        compact = stripped.replace("\n", "").replace("\r", "")
        if any(c.isdigit() for c in compact) or (
            any(c.isupper() for c in compact) and any(c.islower() for c in compact)
        ):
            return "base64"
    return None


def _contains_obfuscation_call(node: ast.AST, aliases: dict[str, str]) -> bool:
    """True if ``node``'s subtree contains a decode/decompress/fromhex call."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            cname = _canonical_name(child.func, aliases)
            if cname and cname in _OBFUSCATION_DECODE:
                return True
            attr = child.func.attr if isinstance(child.func, ast.Attribute) else None
            if attr in _OBFUSCATION_ATTRS:
                return True
    return False


def _is_char_assembly(node: ast.Call) -> Optional[int]:
    """Return the int-literal count if a bytes/bytearray([ints]) is char assembly."""
    arg = _first_arg(node)
    if isinstance(arg, (ast.List, ast.Tuple)):
        ints = [e for e in arg.elts if isinstance(_literal(e), int)]
        if len(ints) >= _CHAR_ASSEMBLY_MIN and len(ints) == len(arg.elts):
            return len(ints)
    return None


# --------------------------------------------------------------------------- #
# Detection — canonical name + call node -> [(capability_type, evidence), ...]
# --------------------------------------------------------------------------- #
def _detect_attr_capabilities(attr: Optional[str], node: ast.Call) -> list[tuple[str, dict]]:
    """Capabilities matched on the bare attribute name (receiver need not be a
    plain name chain, e.g. ``Path('/x').read_text()`` or ``b'..'.fromhex(...)``)."""
    out: list[tuple[str, dict]] = []
    if attr in _FS_PATH_METHODS:
        ev = _filesystem_evidence(attr, node, attr)
        out.append(("filesystem", ev))
        out.extend(_secret_path_records(ev))
    if attr in _OBFUSCATION_ATTRS:
        out.append(("obfuscation", {"api": attr, "kind": "decode"}))
    return out


def _detect_network(name: str, head: str, tail: str, node: ast.Call) -> list[tuple[str, dict]]:
    if name in _NETWORK_EXACT or (head in _NETWORK_HTTP_MODULES and tail in _NETWORK_HTTP_ATTRS):
        return [("network_egress", _network_evidence(name, node))]
    return []


def _detect_process(name: str, head: str, tail: str, node: ast.Call) -> list[tuple[str, dict]]:
    if (
        (head == "subprocess" and tail in _PROC_SUBPROCESS_ATTRS)
        or name in _PROC_OS_EXACT
        or name.startswith("os.exec")
        or name.startswith("os.spawn")
        or name in _PROC_MULTIPROCESSING
        or name == "pty.spawn"
    ):
        return [("process_exec", _process_evidence(name, node))]
    return []


def _detect_filesystem(name: str, head: str, tail: str, node: ast.Call) -> list[tuple[str, dict]]:
    if name in _FS_OPEN or (head == "shutil" and tail in _FS_SHUTIL_ATTRS) or name in _FS_OS_EXACT:
        ev = _filesystem_evidence(name, node, None)
        return [("filesystem", ev), *_secret_path_records(ev)]
    return []


def _detect_dynamic_code(name: str, node: ast.Call, aliases: dict[str, str]) -> list[tuple[str, dict]]:
    if name in _DYNCODE_BUILTINS or name in _DYNCODE_EXACT:
        return [("dynamic_code", _dynamic_code_evidence(name, node, aliases))]
    return []


def _detect_crypto(name: str, head: str, tail: str, node: ast.Call) -> list[tuple[str, dict]]:
    if (
        (head == "hashlib" and tail in _CRYPTO_HASHLIB)
        or head == "hmac"
        or name in _CRYPTO_SSL_EXACT
        or head in _CRYPTO_LIB_HEADS
    ):
        return [("crypto", _crypto_evidence(name, node))]
    return []


def _detect_env_secret(name: str, head: str, node: ast.Call) -> list[tuple[str, dict]]:
    if name in _ENVSECRET_EXACT or head in _ENVSECRET_HEADS:
        return [("env_secret", _env_secret_evidence(name, node))]
    return []


def _detect_serialization(name: str, head: str, tail: str, node: ast.Call) -> list[tuple[str, dict]]:
    if name in _SERIAL_EXACT or (head == "yaml" and tail in _SERIAL_YAML_UNSAFE):
        return [("serialization", _serialization_evidence(name, node))]
    return []


def _detect_obfuscation(name: str, node: ast.Call) -> list[tuple[str, dict]]:
    if name in _OBFUSCATION_DECODE:
        return [("obfuscation", {"api": name, "kind": "decode"})]
    if name in ("bytes", "bytearray"):
        count = _is_char_assembly(node)
        if count is not None:
            return [("obfuscation", {"api": name, "kind": "char_code_assembly", "count": count})]
    return []


def _classify(
    name: Optional[str], node: ast.Call, aliases: dict[str, str]
) -> list[tuple[str, dict]]:
    """Map a resolved call to its capability records (possibly several, or none).

    A single call can yield more than one capability — e.g. reading a secret file
    is both ``filesystem`` and ``env_secret`` — so the result is a list. Each
    capability family is a focused ``_detect_*`` helper; this dispatcher runs them
    in a fixed order and concatenates their records (preserving record ordering).
    """
    # The immediate attribute name is available even when the receiver is not a
    # plain name chain (e.g. ``Path('/x').read_text()`` — receiver is a Call, so
    # ``name`` is None). pathlib's distinctive read/write methods are matched on
    # this attr so a chained construction can't hide a filesystem capability.
    attr = node.func.attr if isinstance(node.func, ast.Attribute) else None
    out = _detect_attr_capabilities(attr, node)

    if not name:
        return out
    head = name.split(".")[0]
    tail = name.rsplit(".", 1)[-1]

    out += _detect_network(name, head, tail, node)
    out += _detect_process(name, head, tail, node)
    out += _detect_filesystem(name, head, tail, node)
    out += _detect_dynamic_code(name, node, aliases)
    out += _detect_crypto(name, head, tail, node)
    out += _detect_env_secret(name, head, node)
    out += _detect_serialization(name, head, tail, node)
    out += _detect_obfuscation(name, node)
    return out


def _secret_path_records(fs_ev: dict) -> list[tuple[str, dict]]:
    """Promote a filesystem *read* of a secret-looking path to an env_secret record."""
    path = fs_ev.get("path")
    mode = fs_ev.get("mode", "")
    if not isinstance(path, str) or "w" in mode or "a" in mode or "x" in mode:
        return []
    low = path.lower()
    if any(marker in low for marker in _SECRET_PATH_MARKERS):
        return [("env_secret", {"api": fs_ev.get("api"), "path": path, "literal": path})]
    return []


# --------------------------------------------------------------------------- #
# AST visitor — tracks the enclosing function for each capability call
# --------------------------------------------------------------------------- #
class _CapabilityVisitor(ast.NodeVisitor):
    """Walk a module, emitting a capability record per matching call site.

    Maintains a stack of enclosing function names so each record carries the
    innermost ``function_name`` (``None`` for module-level code).
    """

    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self._func_stack: list[str] = []
        # Local names assigned from a decode/decompress call — the var half of the
        # ``payload = b64decode(...); exec(payload)`` backdoor. Scoped per function.
        self._tainted: set[str] = set()
        self.records: list[dict] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._func_stack.append(node.name)
        saved = self._tainted
        self._tainted = set(saved)  # inherit enclosing taints (closures)
        self.generic_visit(node)
        self._tainted = saved
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # async defs tracked identically

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Taint any target assigned the result of a decode/decompress call.
        if _contains_obfuscation_call(node.value, self.aliases):
            for tgt in node.targets:
                for sub in ast.walk(tgt):
                    if isinstance(sub, ast.Name):
                        self._tainted.add(sub.id)
        self.generic_visit(node)

    def _emit(self, cap_type: str, evidence: dict, node: ast.AST) -> None:
        """Append one capability record anchored at ``node``'s source span."""
        self.records.append(
            {
                "function_name": self._func_stack[-1] if self._func_stack else None,
                "capability_type": cap_type,
                "evidence": evidence,
                "line_start": node.lineno,
                "line_end": getattr(node, "end_lineno", node.lineno),
                "risk_weight": RISK_WEIGHTS_CAPABILITY.get(cap_type, 0.0),
            }
        )

    def _arg_is_tainted(self, node: ast.Call) -> bool:
        """True if any argument references a decode-tainted local name."""
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Name) and sub.id in self._tainted:
                    return True
        return False

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _canonical_name(node.func, self.aliases)
        for cap_type, evidence in _classify(name, node, self.aliases):
            # exec(payload) where payload = b64decode(...) — link via taint.
            if (
                cap_type == "dynamic_code"
                and not evidence.get("obfuscated_input")
                and self._arg_is_tainted(node)
            ):
                evidence["obfuscated_input"] = True
            self._emit(cap_type, evidence, node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        # os.environ['SECRET'] / environ['SECRET'] — secret read via subscript.
        name = _canonical_name(node.value, self.aliases)
        if name in _ENVSECRET_MAPPING:
            ev: dict[str, Any] = {"api": f"{name}[]"}
            key = _literal(getattr(node, "slice", None))
            # py<3.9 wraps the key in ast.Index; py>=3.9 the slice *is* the node.
            if key is None:
                inner = getattr(node.slice, "value", None)
                key = _literal(inner)
            if isinstance(key, str):
                ev["key"] = key
                ev["literal"] = key
            self._emit("env_secret", ev, node)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        # Long base64/hex string|bytes literals read as a packed/hidden payload.
        kind = _looks_packed(node.value)
        if kind:
            raw = node.value
            preview = raw[:32] if isinstance(raw, str) else raw[:32].decode("ascii", "replace")
            self._emit(
                "obfuscation",
                {"kind": f"{kind}_literal", "length": len(raw), "preview": preview},
                node,
            )
        self.generic_visit(node)


# --------------------------------------------------------------------------- #
# Public extraction API
# --------------------------------------------------------------------------- #
def _rel(path: Path, root: Path) -> str:
    """Path relative to the scan root (portable); original string on cross-drive."""
    try:
        return os.path.relpath(path, root)
    except (ValueError, OSError):
        return str(path)


def _extract_file(file_path: Path, root: Path) -> list[dict]:
    """Extract capability records from a single ``.py`` file.

    A read error or syntax error yields ``[]`` (a file we cannot parse statically
    contributes no capabilities) — never an exception that aborts a tree scan.
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        logger.warning("capability_extractor: cannot read %s: %s", file_path, exc)
        return []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        logger.warning("capability_extractor: cannot parse %s: %s", file_path, exc)
        return []

    visitor = _CapabilityVisitor(_build_aliases(tree))
    visitor.visit(tree)

    rel = _rel(file_path, root)
    for rec in visitor.records:
        rec["file_path"] = rel
    return visitor.records


def _iter_py_files(root: Path):
    """Yield ``.py``/``.pyw`` files under ``root``, skipping vendored/cache dirs."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        for fn in files:
            if fn.endswith((".py", ".pyw")):
                fp = Path(dirpath) / fn
                try:
                    if fp.stat().st_size > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield fp


def extract(path: str | os.PathLike) -> list[dict]:
    """Extract the Phase-1 capability manifest for a file or directory tree.

    Args:
        path: a single ``.py`` file or a directory to walk recursively.

    Returns:
        A list of capability records, each
        ``{file_path, function_name, capability_type, evidence, line_start,
        line_end, risk_weight}``. ``file_path`` is relative to ``path`` (for a
        directory) or to the file's parent (for a single file). ``evidence`` is a
        plain dict (JSON-serializable) — it is ``json.dumps``-encoded only at the
        persistence boundary.
    """
    root = Path(path)
    records: list[dict] = []
    if root.is_dir():
        for fp in _iter_py_files(root):
            records.extend(_extract_file(fp, root))
    elif root.is_file():
        records.extend(_extract_file(root, root.parent))
    else:
        logger.warning("capability_extractor: path does not exist: %s", root)
    return records


# --------------------------------------------------------------------------- #
# Persistence — append-only to integrity_capabilities
# --------------------------------------------------------------------------- #
_CAPABILITY_SQL = (
    "INSERT INTO integrity_capabilities "
    "(assessment_id, file_path, function_name, capability_type, evidence, "
    "line_start, line_end, risk_weight, tenant_id, classification) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _insert_capability(conn: Any, params: tuple) -> int:
    """Insert one append-only ``integrity_capabilities`` row; return its PK."""
    if _backend_of(conn) == "postgresql":
        cur = conn.execute(_CAPABILITY_SQL.replace("?", "%s") + " RETURNING id", params)
        row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    cur = conn.execute(_CAPABILITY_SQL, params)
    conn.commit()
    return int(cur.lastrowid or 0)


def _persist(conn: Any, assessment_id: int, records: list[dict]) -> list[int]:
    """Append every capability record to ``integrity_capabilities``; return ids."""
    tenant_id, classification, _ = _caller_context()
    ids: list[int] = []
    for rec in records:
        cap_id = _insert_capability(
            conn,
            (
                assessment_id,
                rec["file_path"],
                rec.get("function_name"),
                rec["capability_type"],
                json.dumps(rec["evidence"]),
                rec.get("line_start"),
                rec.get("line_end"),
                rec.get("risk_weight", 0.0),
                tenant_id,
                classification,
            ),
        )
        ids.append(cap_id)
    return ids


def extract_and_persist(assessment_id: int, path: str | os.PathLike, conn: Any = None) -> dict:
    """Extract the capability manifest for ``path`` and persist it append-only.

    Opens an RLS-aware connection when ``conn`` is ``None`` (closing it on exit);
    ``init_db`` is invoked idempotently so this can run standalone before any
    assessment's tables exist. Returns a summary
    ``{"assessment_id", "capabilities_persisted", "capability_ids", "by_type"}``.
    """
    records = extract(path)

    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection

        conn = get_connection()
    try:
        init_db(conn)  # idempotent: CREATE TABLE IF NOT EXISTS
        ids = _persist(conn, assessment_id, records)
    finally:
        if own_conn:
            conn.close()

    by_type: dict[str, int] = {}
    for rec in records:
        by_type[rec["capability_type"]] = by_type.get(rec["capability_type"], 0) + 1

    return {
        "assessment_id": assessment_id,
        "capabilities_persisted": len(ids),
        "capability_ids": ids,
        "by_type": by_type,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="SIPA capability extractor — Python AST -> integrity_capabilities "
        "(network_egress / filesystem / process_exec / dynamic_code / crypto / "
        "env_secret / serialization / obfuscation)"
    )
    parser.add_argument("--path", required=True, help="file or directory to scan")
    parser.add_argument(
        "--assessment-id",
        type=int,
        default=None,
        help="integrity_assessments.id to attach + persist capabilities to "
        "(omit to print the manifest without persisting)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()

    if args.assessment_id is not None:
        result = extract_and_persist(args.assessment_id, args.path)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"SIPA capabilities — assessment {result['assessment_id']}")
            for cap_type, n in sorted(result["by_type"].items()):
                print(f"  {cap_type}: {n}")
            print(f"  total: {result['capabilities_persisted']} capability record(s) persisted")
        return

    records = extract(args.path)
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        print(f"SIPA capability manifest — {args.path}")
        for rec in records:
            loc = f"{rec['file_path']}:{rec['line_start']}"
            fn = rec["function_name"] or "<module>"
            print(f"  [{rec['capability_type']}] {loc} in {fn}() — {rec['evidence'].get('api', '')}")
        print(f"  total: {len(records)} capability record(s)")


if __name__ == "__main__":
    main()
