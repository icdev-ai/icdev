# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — AST capability extractor.

Parses Python source into a behavioral capability manifest without importing or
executing the target. Produces records consumed by ``engine.assess`` and the
SIPA reconcilers.
"""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any

from tools.integrity.constants import CAPABILITY_TYPES, RISK_WEIGHTS_CAPABILITY


# --------------------------------------------------------------------------- #
# Detector configuration
# --------------------------------------------------------------------------- #

_SECRET_PATH_MARKERS = (".env",)
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# Minimum lengths for obfuscation literal heuristics.
_MIN_BASE64_LITERAL_LEN = 40
_MIN_HEX_LITERAL_LEN = 32
_MIN_CHAR_CODE_LEN = 5

# Method-name dispatch: attribute call by attr name -> (capability_type, api label,
# kwarg overrides for evidence).
_METHOD_DISPATCH: dict[str, tuple[str, str, dict[str, Any]]] = {
    # filesystem — pathlib / os / shutil
    "read_text":   ("filesystem", "read_text", {"mode": "r"}),
    "read_bytes":  ("filesystem", "read_bytes", {"mode": "rb"}),
    "write_text":  ("filesystem", "write_text", {"mode": "w"}),
    "write_bytes": ("filesystem", "write_bytes", {"mode": "wb"}),
    "copy":        ("filesystem", "shutil.copy", {}),
    "copy2":       ("filesystem", "shutil.copy2", {}),
    "move":        ("filesystem", "shutil.move", {}),
    "rmtree":      ("filesystem", "shutil.rmtree", {}),
    "remove":      ("filesystem", "os.remove", {}),
    "unlink":      ("filesystem", "os.unlink", {}),
    "rmdir":       ("filesystem", "os.rmdir", {}),
    "mkdir":       ("filesystem", "os.mkdir", {}),
    "makedirs":    ("filesystem", "os.makedirs", {}),
    "rename":      ("filesystem", "os.rename", {}),
    "renames":     ("filesystem", "os.renames", {}),
    # network
    "connect":     ("network_egress", "socket.connect", {}),
    "sendall":     ("network_egress", "socket.sendall", {}),
    "send":        ("network_egress", "socket.send", {}),
    "urlopen":     ("network_egress", "urllib.request.urlopen", {}),
    # process_exec (subprocess methods on instances are rare, but keep for coverage)
    "popen":       ("process_exec", "os.popen", {}),
    # obfuscation
    "b64decode":   ("obfuscation", "base64.b64decode", {"kind": "decode", "decode_type": "base64"}),
    "b64encode":   ("obfuscation", "base64.b64encode", {"kind": "encode", "decode_type": "base64"}),
    "decompress":  ("obfuscation", "zlib.decompress", {"kind": "decode", "decode_type": "zlib"}),
    "unhexlify":   ("obfuscation", "binascii.unhexlify", {"kind": "decode", "decode_type": "hex"}),
}

# Function-call dispatch by canonical dotted name -> (capability_type, api label,
# extra evidence defaults).
_FUNCTION_DISPATCH: dict[str, tuple[str, str, dict[str, Any]]] = {
    # network
    "socket.socket":              ("network_egress", "socket.socket", {}),
    "socket.create_connection":   ("network_egress", "socket.create_connection", {}),
    "socket.getaddrinfo":         ("network_egress", "socket.getaddrinfo", {}),
    "http.client.HTTPConnection": ("network_egress", "http.client.HTTPConnection", {}),
    "http.client.HTTPSConnection":("network_egress", "http.client.HTTPSConnection", {}),
    "urllib.request.urlopen":     ("network_egress", "urllib.request.urlopen", {}),
    "urllib.request.Request":     ("network_egress", "urllib.request.Request", {}),
    # requests / httpx family
    "requests.get":               ("network_egress", "requests.get", {}),
    "requests.post":              ("network_egress", "requests.post", {}),
    "requests.put":               ("network_egress", "requests.put", {}),
    "requests.delete":            ("network_egress", "requests.delete", {}),
    "requests.head":              ("network_egress", "requests.head", {}),
    "requests.patch":             ("network_egress", "requests.patch", {}),
    "requests.options":           ("network_egress", "requests.options", {}),
    "requests.request":           ("network_egress", "requests.request", {}),
    "httpx.get":                  ("network_egress", "httpx.get", {}),
    "httpx.post":                 ("network_egress", "httpx.post", {}),
    "httpx.put":                  ("network_egress", "httpx.put", {}),
    "httpx.delete":               ("network_egress", "httpx.delete", {}),
    "httpx.head":                 ("network_egress", "httpx.head", {}),
    "httpx.patch":                ("network_egress", "httpx.patch", {}),
    "httpx.options":              ("network_egress", "httpx.options", {}),
    "httpx.request":              ("network_egress", "httpx.request", {}),
    # filesystem
    "open":                       ("filesystem", "open", {}),
    "pathlib.Path.read_text":     ("filesystem", "read_text", {"mode": "r"}),
    "pathlib.Path.read_bytes":    ("filesystem", "read_bytes", {"mode": "rb"}),
    "pathlib.Path.write_text":    ("filesystem", "write_text", {"mode": "w"}),
    "pathlib.Path.write_bytes":   ("filesystem", "write_bytes", {"mode": "wb"}),
    "shutil.copy":                ("filesystem", "shutil.copy", {}),
    "shutil.copy2":               ("filesystem", "shutil.copy2", {}),
    "shutil.copytree":            ("filesystem", "shutil.copytree", {}),
    "shutil.move":                ("filesystem", "shutil.move", {}),
    "shutil.rmtree":              ("filesystem", "shutil.rmtree", {}),
    "os.remove":                  ("filesystem", "os.remove", {}),
    "os.unlink":                  ("filesystem", "os.unlink", {}),
    "os.rmdir":                   ("filesystem", "os.rmdir", {}),
    "os.mkdir":                   ("filesystem", "os.mkdir", {}),
    "os.makedirs":                ("filesystem", "os.makedirs", {}),
    "os.rename":                  ("filesystem", "os.rename", {}),
    "os.renames":                 ("filesystem", "os.renames", {}),
    # process_exec
    "subprocess.run":             ("process_exec", "subprocess.run", {}),
    "subprocess.call":            ("process_exec", "subprocess.call", {}),
    "subprocess.check_call":      ("process_exec", "subprocess.check_call", {}),
    "subprocess.check_output":    ("process_exec", "subprocess.check_output", {}),
    "subprocess.Popen":           ("process_exec", "subprocess.Popen", {}),
    "os.system":                  ("process_exec", "os.system", {}),
    "os.popen":                   ("process_exec", "os.popen", {}),
    # dynamic_code
    "eval":                       ("dynamic_code", "eval", {}),
    "exec":                       ("dynamic_code", "exec", {}),
    "compile":                    ("dynamic_code", "compile", {}),
    "__import__":                 ("dynamic_code", "__import__", {}),
    "importlib.import_module":    ("dynamic_code", "importlib.import_module", {}),
    "types.FunctionType":         ("dynamic_code", "types.FunctionType", {}),
    "types.CodeType":             ("dynamic_code", "types.CodeType", {}),
    # crypto
    "hashlib.md5":                ("crypto", "hashlib.md5", {"algorithm": "md5"}),
    "hashlib.sha1":               ("crypto", "hashlib.sha1", {"algorithm": "sha1"}),
    "hashlib.sha224":             ("crypto", "hashlib.sha224", {"algorithm": "sha224"}),
    "hashlib.sha256":             ("crypto", "hashlib.sha256", {"algorithm": "sha256"}),
    "hashlib.sha384":             ("crypto", "hashlib.sha384", {"algorithm": "sha384"}),
    "hashlib.sha512":             ("crypto", "hashlib.sha512", {"algorithm": "sha512"}),
    "hashlib.new":                ("crypto", "hashlib.new", {}),
    "ssl._create_unverified_context": ("crypto", "ssl._create_unverified_context", {"insecure": True}),
    # env_secret
    "os.environ":                 ("env_secret", "os.environ[]", {}),
    "os.getenv":                  ("env_secret", "os.getenv", {}),
    "os.environ.get":             ("env_secret", "os.environ.get", {}),
    "keyring.get_password":       ("env_secret", "keyring.get_password", {}),
    # serialization
    "pickle.load":                ("serialization", "pickle.load", {"deserialize": True}),
    "pickle.loads":               ("serialization", "pickle.loads", {"deserialize": True}),
    "pickle.dump":                ("serialization", "pickle.dump", {}),
    "pickle.dumps":               ("serialization", "pickle.dumps", {}),
    "marshal.load":               ("serialization", "marshal.load", {"deserialize": True}),
    "marshal.loads":              ("serialization", "marshal.loads", {"deserialize": True}),
    "marshal.dump":               ("serialization", "marshal.dump", {}),
    "marshal.dumps":              ("serialization", "marshal.dumps", {}),
    "shelve.open":                ("serialization", "shelve.open", {}),
    "yaml.load":                  ("serialization", "yaml.load", {}),
    # obfuscation decoders
    "base64.b64decode":           ("obfuscation", "base64.b64decode", {"kind": "decode", "decode_type": "base64"}),
    "base64.b64encode":           ("obfuscation", "base64.b64encode", {"kind": "encode", "decode_type": "base64"}),
    "zlib.decompress":            ("obfuscation", "zlib.decompress", {"kind": "decode", "decode_type": "zlib"}),
    "binascii.unhexlify":         ("obfuscation", "binascii.unhexlify", {"kind": "decode", "decode_type": "hex"}),
    "binascii.hexlify":           ("obfuscation", "binascii.hexlify", {"kind": "encode", "decode_type": "hex"}),
}

# Network HTTP methods supported by requests/httpx/etc.
_HTTP_METHODS = {"get", "post", "put", "delete", "head", "patch", "options", "request"}
_HTTP_LIBRARIES = {"requests", "httpx", "urllib3", "aiohttp"}

# Weak algorithms (lower-cased) for hashlib.new.
_WEAK_HASHES = {"md5", "sha1"}


# --------------------------------------------------------------------------- #
# Visitor
# --------------------------------------------------------------------------- #

class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, root: str = "") -> None:
        self.file_path = file_path
        self.root = root
        self.records: list[dict[str, Any]] = []
        self.scope: list[str] = []
        self.aliases: dict[str, str] = {}
        # Names bound in local scope that are derived from obfuscation/decoding.
        self.obfuscated_vars: set[str] = set()

    # ------------------------------------------------------------------ #
    # Scope helpers
    # ------------------------------------------------------------------ #
    def _relative_path(self) -> str:
        if self.root:
            try:
                return os.path.relpath(self.file_path, self.root).replace(os.sep, "/")
            except ValueError:
                pass
        return Path(self.file_path).name

    def _function_name(self) -> str:
        return ".".join(self.scope) if self.scope else ""

    def _line_span(self, node: ast.AST) -> tuple[int, int]:
        return (
            getattr(node, "lineno", 1) or 1,
            getattr(node, "end_lineno", getattr(node, "lineno", 1)) or 1,
        )

    def _add_record(
        self,
        capability_type: str,
        evidence: dict[str, Any],
        node: ast.AST,
    ) -> dict[str, Any]:
        line_start, line_end = self._line_span(node)
        rec: dict[str, Any] = {
            "file_path": self._relative_path(),
            "function_name": self._function_name(),
            "capability_type": capability_type,
            "evidence": evidence,
            "line_start": line_start,
            "line_end": line_end,
            "risk_weight": RISK_WEIGHTS_CAPABILITY.get(capability_type, 0.0),
        }
        self.records.append(rec)
        return rec

    # ------------------------------------------------------------------ #
    # Import tracking
    # ------------------------------------------------------------------ #
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            self.aliases[name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            name = alias.asname if alias.asname else alias.name
            if module:
                self.aliases[name] = f"{module}.{alias.name}"
            else:
                self.aliases[name] = alias.name
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    # ------------------------------------------------------------------ #
    # Name resolution
    # ------------------------------------------------------------------ #
    def _resolve(self, node: ast.AST | None) -> str:
        """Return a canonical dotted name for a Name/Attribute expression."""
        if node is None:
            return ""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Edge: e.g. getattr literal module name; not expected.
            return node.value
        return ""

    def _str_arg(self, args: list[ast.expr], keywords: list[ast.keyword], key: str, index: int = 0) -> str | None:
        """Extract a string literal argument by position or keyword."""
        for kw in keywords:
            if kw.arg == key:
                return self._literal_str(kw.value)
        if index < len(args):
            return self._literal_str(args[index])
        return None

    def _literal_str(self, node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr) and len(node.values) == 1:
            return self._literal_str(node.values[0])  # type: ignore[arg-type]
        return None

    def _literal_int_list(self, node: ast.expr | None) -> list[int] | None:
        if isinstance(node, ast.List):
            out: list[int] = []
            for elt in node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                    out.append(elt.value)
                else:
                    return None
            return out
        return None

    def _static_str_value(self, node: ast.expr | None) -> str | None:
        """Evaluate a compile-time string expression (e.g. ``'a' * 10``)."""
        if node is None:
            return None
        try:
            value = ast.literal_eval(node)
        except Exception:
            return None
        return value if isinstance(value, str) else None

    def _is_secret_path(self, path: str | None) -> bool:
        if not path:
            return False
        lower = path.lower()
        return any(marker in lower for marker in _SECRET_PATH_MARKERS)

    # ------------------------------------------------------------------ #
    # Obfuscation helpers
    # ------------------------------------------------------------------ #
    def _contains_obfuscation_call(self, node: ast.expr | None) -> bool:
        if node is None:
            return False
        if isinstance(node, ast.Call):
            canonical = self._resolve(node.func)
            if canonical in {"base64.b64decode", "binascii.unhexlify", "zlib.decompress"}:
                return True
            # Check any argument / the called expression recursively.
            if self._contains_obfuscation_call(node.func):
                return True
            for arg in node.args:
                if self._contains_obfuscation_call(arg):
                    return True
            for kw in node.keywords:
                if self._contains_obfuscation_call(kw.value):
                    return True
            return False
        if isinstance(node, ast.Attribute):
            return self._contains_obfuscation_call(node.value)
        if isinstance(node, ast.BinOp):
            return self._contains_obfuscation_call(node.left) or self._contains_obfuscation_call(node.right)
        return False

    def _arg_is_obfuscated(self, node: ast.expr | None) -> bool:
        """Return True if an argument is an obfuscated variable or decode call."""
        if node is None:
            return False
        if isinstance(node, ast.Name) and node.id in self.obfuscated_vars:
            return True
        return self._contains_obfuscation_call(node)

    def _scan_for_obfuscated_literals(self, node: ast.AST) -> None:
        """Walk a constant/literal node and emit obfuscation records."""
        # ``ast.literal_eval`` is safe and can resolve simple string operations
        # like ``'deadbeef' * 10`` without executing user code.
        expr = node if isinstance(node, ast.expr) else None
        val = self._static_str_value(expr) if expr else None
        if val is None and isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
        if not val or not isinstance(val, str):
            return
        # Hex literals must be detected before the broader base64 alphabet so a
        # pure hex string like ``'deadbeef' * 10`` is classified correctly.
        if len(val) >= _MIN_HEX_LITERAL_LEN and len(val) % 2 == 0 and _HEX_RE.match(val):
            self._add_record(
                "obfuscation",
                {"kind": "hex_literal", "length": len(val)},
                node,
            )
        elif len(val) >= _MIN_BASE64_LITERAL_LEN and _BASE64_RE.match(val):
            self._add_record(
                "obfuscation",
                {"kind": "base64_literal", "length": len(val)},
                node,
            )

    # ------------------------------------------------------------------ #
    # Call dispatch
    # ------------------------------------------------------------------ #
    def visit_Call(self, node: ast.Call) -> None:
        canonical = self._resolve(node.func)
        attr_name = ""
        base_name = ""
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            base_name = self._resolve(node.func.value)

        # First pass: let method dispatch catch attribute calls where we only
        # know the method name (e.g. ``s.connect`` whose base object is unknown).
        handled = False
        if attr_name:
            if attr_name in _HTTP_METHODS and base_name:
                base_module = base_name.split('.')[0]
                if base_module in _HTTP_LIBRARIES:
                    # requests/httpx/urllib3 method calls: base alias resolves to the library.
                    api = f"{base_module}.{attr_name}"
                    url = self._str_arg(node.args, node.keywords, "url", 0)
                    evidence: dict[str, Any] = {"api": api}
                    if url:
                        evidence["url"] = url
                    self._add_record("network_egress", evidence, node)
                    handled = True
            elif attr_name == "connect" and not handled:
                host = None
                if node.args and isinstance(node.args[0], (ast.Tuple, ast.List)) and node.args[0].elts:
                    host = self._literal_str(node.args[0].elts[0])
                if host is None:
                    host = self._str_arg(node.args, node.keywords, "host", 0)
                evidence = {"api": "socket.connect"}
                if host:
                    evidence["host"] = host
                self._add_record("network_egress", evidence, node)
                handled = True
            elif attr_name in _METHOD_DISPATCH and not handled:
                cap_type, api, defaults = _METHOD_DISPATCH[attr_name]
                evidence = dict(defaults)
                evidence["api"] = api

                # Pull receiver / path evidence where the API operates on a path.
                if attr_name in {"read_text", "read_bytes", "write_text", "write_bytes", "copy", "copy2", "move", "rmtree", "remove", "unlink", "mkdir", "makedirs", "rmdir", "rename", "renames"}:
                    if attr_name in {"copy", "copy2", "move"}:
                        src = self._str_arg(node.args, node.keywords, "src", 0)
                        dst = self._str_arg(node.args, node.keywords, "dst", 1)
                        if src is not None:
                            evidence["src"] = src
                        if dst is not None:
                            evidence["dst"] = dst
                    elif node.func and isinstance(node.func, ast.Attribute):
                        path = self._literal_str(node.func.value)
                        if path is not None:
                            evidence["path"] = path
                    else:
                        path = self._str_arg(node.args, node.keywords, "path", 0)
                        if path is not None:
                            evidence["path"] = path

                if cap_type == "filesystem" and "path" in evidence:
                    # open-style secret-path reads are also env_secret.
                    if self._is_secret_path(evidence.get("path")) and evidence.get("mode", "").startswith("r"):
                        self._add_record(
                            "env_secret",
                            {"api": f"{api}", "path": evidence["path"]},
                            node,
                        )

                self._add_record(cap_type, evidence, node)
                handled = True

        # Second pass: canonical function-name dispatch.
        if not handled and canonical in _FUNCTION_DISPATCH:
            cap_type, api, defaults = _FUNCTION_DISPATCH[canonical]
            evidence = dict(defaults)
            evidence["api"] = api

            # Fill in call-site-specific evidence.
            if cap_type == "network_egress":
                url = self._str_arg(node.args, node.keywords, "url", 0)
                if url is not None:
                    evidence["url"] = url
                host = self._str_arg(node.args, node.keywords, "host", 0)
                if host is not None:
                    evidence["host"] = host
            elif cap_type == "filesystem" and api == "open":
                path = self._str_arg(node.args, node.keywords, "file", 0)
                mode = self._str_arg(node.args, node.keywords, "mode", 1) or "r"
                if path is not None:
                    evidence["path"] = path
                evidence["mode"] = mode
                # Reading a secret-looking path is also an env_secret hit.
                if self._is_secret_path(path) and mode.startswith("r"):
                    self._add_record(
                        "env_secret",
                        {"api": "open", "path": path},
                        node,
                    )
            elif cap_type == "filesystem" and "pathlib" in api:
                # Receiver path captured via method dispatch above; fallback here.
                if "path" not in evidence and node.func and isinstance(node.func, ast.Attribute):
                    path = self._literal_str(node.func.value)
                    if path is not None:
                        evidence["path"] = path
            elif cap_type == "process_exec":
                if api == "subprocess.run" and node.args:
                    cmd = node.args[0]
                    if isinstance(cmd, (ast.List, ast.Tuple)):
                        command = [self._literal_str(a) for a in cmd.elts]
                        if all(isinstance(x, str) for x in command):
                            evidence["command"] = command
                    else:
                        literal = self._literal_str(cmd)
                        if literal is not None:
                            evidence["command"] = literal
                elif api in {"os.system", "os.popen"}:
                    literal = self._str_arg(node.args, node.keywords, "command", 0)
                    if literal is not None:
                        evidence["command"] = literal
            elif cap_type == "env_secret":
                if api == "os.environ[]":
                    key = self._str_arg(node.args, node.keywords, "key", 0)
                    if key is not None:
                        evidence["key"] = key
                elif api in {"os.getenv", "os.environ.get", "keyring.get_password"}:
                    key = self._str_arg(node.args, node.keywords, "key", 0)
                    if key is None and api == "keyring.get_password":
                        key = self._str_arg(node.args, node.keywords, "username", 1)
                    if key is not None:
                        evidence["key"] = key
            elif cap_type == "crypto" and api == "hashlib.new":
                algo = self._str_arg(node.args, node.keywords, "name", 0)
                if algo is not None:
                    evidence["algorithm"] = algo
                    if algo.lower() in _WEAK_HASHES:
                        evidence["weak"] = True
            elif cap_type == "crypto":
                algo = evidence.get("algorithm")
                if algo and algo.lower() in _WEAK_HASHES:
                    evidence["weak"] = True
            elif cap_type == "serialization" and api == "yaml.load":
                loader = None
                for kw in node.keywords:
                    if kw.arg == "Loader":
                        loader = self._resolve(kw.value)
                safe = loader is not None and "SafeLoader" in loader
                evidence["safe_loader"] = safe

            if cap_type == "dynamic_code":
                for arg in node.args:
                    if self._arg_is_obfuscated(arg):
                        evidence["obfuscated_input"] = True
                        break
                if "obfuscated_input" not in evidence:
                    for kw in node.keywords:
                        if kw.arg in {"source", "code", "string", "object", "globals"} and self._arg_is_obfuscated(kw.value):
                            evidence["obfuscated_input"] = True
                            break

            self._add_record(cap_type, evidence, node)
            handled = True

        # Special: ``bytes([...])`` -> char_code_assembly obfuscation.
        if not handled and canonical == "bytes":
            ints = self._literal_int_list(node.args[0] if node.args else None)
            if ints and len(ints) >= _MIN_CHAR_CODE_LEN:
                self._add_record(
                    "obfuscation",
                    {"api": "bytes", "kind": "char_code_assembly", "length": len(ints)},
                    node,
                )

        # Track obfuscated assignments and dynamic-code inputs.
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Scan RHS for obfuscation calls / literals before descending.
        for target in node.targets:
            if isinstance(target, ast.Name) and self._contains_obfuscation_call(node.value):
                self.obfuscated_vars.add(target.id)
        self._scan_for_obfuscated_literals(node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value and self._contains_obfuscation_call(node.value):
            self.obfuscated_vars.add(node.target.id)
        if node.value:
            self._scan_for_obfuscated_literals(node.value)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        # Module-level long string literals can be obfuscation too.
        self._scan_for_obfuscated_literals(node.value)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Catch standalone constants assigned at module/function scope.
        self._scan_for_obfuscated_literals(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        canonical = self._resolve(node.value)
        if canonical == "os.environ":
            key = self._str_arg([node.slice] if not isinstance(node.slice, ast.Slice) else [], [], "key", 0)
            evidence = {"api": "os.environ[]"}
            if key is not None:
                evidence["key"] = key
            self._add_record("env_secret", evidence, node)
        self.generic_visit(node)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def extract(source: str) -> list[dict[str, Any]]:
    """Return a capability manifest for a Python file or directory tree.

    For a directory, only ``.py`` files are scanned; ``__pycache__`` and hidden
    VCS directories are skipped. Unparseable files are silently ignored.
    """
    path = Path(source)
    if not path.exists():
        return []

    records: list[dict[str, Any]] = []

    def _scan_file(file_path: Path, root: str) -> None:
        try:
            text = file_path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(file_path))
        except Exception:
            return
        visitor = _CapabilityVisitor(str(file_path), root)
        visitor.visit(tree)
        records.extend(visitor.records)

    if path.is_file():
        _scan_file(path, "")
    elif path.is_dir():
        for py_file in path.rglob("*.py"):
            if any(part.startswith(".") or part == "__pycache__" for part in py_file.relative_to(path).parts):
                continue
            _scan_file(py_file, str(path))

    return records


def _persist(conn: Any, assessment_id: int, manifest: list[dict[str, Any]]) -> None:
    """Append manifest rows to ``integrity_capabilities``."""
    for rec in manifest:
        conn.execute(
            "INSERT INTO integrity_capabilities "
            "(assessment_id, file_path, function_name, capability_type, evidence, line_start, line_end, risk_weight) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                assessment_id,
                rec.get("file_path", ""),
                rec.get("function_name", ""),
                rec["capability_type"],
                json.dumps(rec.get("evidence", {})),
                rec.get("line_start"),
                rec.get("line_end"),
                rec.get("risk_weight", 0.0),
            ),
        )
    conn.commit()


def extract_and_persist(
    assessment_id: int,
    source: str,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Extract capabilities from ``source`` and persist them append-only.

    Returns:
        {"assessment_id": int, "capabilities_persisted": int, "by_type": dict}
    """
    manifest = extract(source)
    own_conn = conn is None
    if own_conn:
        from tools.db.storage import get_connection
        conn = get_connection()
    try:
        from tools.integrity.db.init_db import init_db

        init_db(conn)
        _persist(conn, assessment_id, manifest)
        by_type: dict[str, int] = {}
        for rec in manifest:
            by_type[rec["capability_type"]] = by_type.get(rec["capability_type"], 0) + 1
        return {
            "assessment_id": assessment_id,
            "capabilities_persisted": len(manifest),
            "by_type": by_type,
        }
    finally:
        if own_conn:
            conn.close()
