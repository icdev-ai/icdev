# CUI // SP-CTI
"""SIPA capability extraction beyond Python: Java, C#, JavaScript/TypeScript and Go (icx-sipa-01).

``capability_extractor`` reads Python through its AST. Legacy applications being refactored are mostly not Python,
so for these languages this module finds the same eight capability types by LEXICAL match on the source with
comments removed (line numbers kept). Every record says so -- ``evidence.method = "lexical"`` -- because a lexical
match is weaker evidence than an AST one: it can be fooled by an identifier that merely looks like an API, and it
cannot follow an alias. It never executes or imports the target.

A source file in a language neither extractor reads is UNASSESSED, never clean: ``coverage()`` lists those files
by extension, and ``engine.assess`` reports them beside the verdict.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.integrity.constants import RISK_WEIGHTS_CAPABILITY

LANGUAGES = {".java": "java", ".cs": "csharp", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
             ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go"}
#: Source extensions SIPA recognises as code it cannot read (reported as unassessed, never as clean).
UNREAD = {".c", ".h", ".cpp", ".cc", ".hpp", ".rs", ".rb", ".php", ".kt", ".kts", ".scala", ".swift", ".vb",
          ".cbl", ".cob", ".cpy", ".pl", ".pm", ".lua", ".r", ".m", ".f90", ".for", ".pas", ".ps1", ".sh", ".bat"}
_SKIP = {".git", "node_modules", "__pycache__", "vendor", "dist", "build", "target", "bin", "obj"}
_MIN_BASE64 = 40

_C_FAMILY = {
    "network_egress": [
        ("java", r"\bnew\s+Socket\s*\(", "java.net.Socket"), ("java", r"\.openConnection\s*\(", "URL.openConnection"),
        ("java", r"\bHttpClient\.new(?:HttpClient|Builder)\b", "java.net.http.HttpClient"),
        ("java", r"\bnew\s+OkHttpClient\b", "okhttp3.OkHttpClient"),
        ("csharp", r"\bnew\s+(?:HttpClient|WebClient|TcpClient|UdpClient)\s*\(", "System.Net client"),
        ("csharp", r"\bWebRequest\.Create\s*\(", "WebRequest.Create"),
        ("javascript", r"(?<![\w.])fetch\s*\(", "fetch"), ("javascript", r"\baxios(?:\.\w+)?\s*\(", "axios"),
        ("javascript", r"\bhttps?\.(?:request|get)\s*\(", "http.request"), ("javascript", r"\bnew\s+WebSocket\s*\(", "WebSocket"),
        ("javascript", r"\bnew\s+XMLHttpRequest\s*\(", "XMLHttpRequest"), ("javascript", r"\bnet\.(?:connect|createConnection)\s*\(", "net.connect"),
        ("go", r"\bnet\.Dial(?:Timeout)?\s*\(", "net.Dial"), ("go", r"\bhttp\.(?:Get|Post|PostForm|NewRequest(?:WithContext)?)\s*\(", "net/http"),
    ],
    "filesystem": [
        ("java", r"\bnew\s+(?:FileOutputStream|FileWriter|RandomAccessFile)\s*\(", "java.io file write"),
        ("java", r"\bFiles\.(?:write|writeString|delete|deleteIfExists|move|copy|newBufferedWriter|readAllBytes|readString)\s*\(", "java.nio.file.Files"),
        ("csharp", r"\bFile\.(?:Write\w*|Append\w*|Delete|Move|Copy|ReadAll\w*|Open\w*)\s*\(", "System.IO.File"),
        ("csharp", r"\bDirectory\.(?:Delete|Move|CreateDirectory)\s*\(", "System.IO.Directory"), ("csharp", r"\bnew\s+FileStream\s*\(", "FileStream"),
        ("javascript", r"\bfs(?:\.promises)?\.(?:writeFile|appendFile|unlink|rm|rmdir|rename|readFile|createWriteStream)\w*\s*\(", "node fs"),
        ("go", r"\b(?:os|ioutil)\.(?:WriteFile|Remove|RemoveAll|Create|OpenFile|Rename|ReadFile)\s*\(", "os file"),
    ],
    "process_exec": [
        ("java", r"\bRuntime\.getRuntime\(\)\s*\.exec\s*\(", "Runtime.exec"), ("java", r"\bnew\s+ProcessBuilder\s*\(", "ProcessBuilder"),
        ("csharp", r"\bProcess\.Start\s*\(", "Process.Start"), ("csharp", r"\bnew\s+ProcessStartInfo\s*\(", "ProcessStartInfo"),
        ("javascript", r"\bchild_process\b", "child_process"), ("javascript", r"\b(?:execSync|execFile|execFileSync|spawn|spawnSync)\s*\(", "child_process call"),
        ("go", r"\bexec\.Command(?:Context)?\s*\(", "os/exec.Command"),
    ],
    "dynamic_code": [
        ("java", r"\bClass\.forName\s*\(", "Class.forName"), ("java", r"\bnew\s+ScriptEngineManager\s*\(", "ScriptEngineManager"),
        ("java", r"\.defineClass\s*\(", "ClassLoader.defineClass"),
        ("csharp", r"\bAssembly\.Load\w*\s*\(", "Assembly.Load"), ("csharp", r"\bActivator\.CreateInstance\s*\(", "Activator.CreateInstance"),
        ("csharp", r"\b(?:CSharpCodeProvider|DynamicMethod|CSharpScript)\b", "runtime codegen"),
        ("javascript", r"(?<![\w.])eval\s*\(", "eval"), ("javascript", r"\bnew\s+Function\s*\(", "new Function"),
        ("javascript", r"\bvm\.run\w*\s*\(", "vm.run"),
        ("go", r"\bplugin\.Open\s*\(", "plugin.Open"),
    ],
    "crypto": [
        ("java", r"\b(?:Cipher|MessageDigest|KeyGenerator|Signature|Mac|KeyPairGenerator)\.getInstance\s*\(", "javax.crypto / java.security"),
        ("csharp", r"\b(?:Aes|RSA|ECDsa|SHA\d+|HMACSHA\d+|MD5)\.Create\s*\(", "System.Security.Cryptography"),
        ("javascript", r"\bcrypto\.(?:create\w+|subtle|randomBytes|pbkdf2\w*|scrypt\w*)\b", "node crypto"),
        ("go", r"\b(?:aes\.NewCipher|sha256\.New|sha512\.New|rsa\.\w+|hmac\.New|ecdsa\.\w+)\s*\(", "go crypto"),
    ],
    "env_secret": [
        ("java", r"\bSystem\.getenv\s*\(", "System.getenv"),
        ("csharp", r"\bEnvironment\.GetEnvironmentVariable\w*\s*\(", "Environment.GetEnvironmentVariable"),
        ("javascript", r"\bprocess\.env\b", "process.env"),
        ("go", r"\bos\.(?:Getenv|LookupEnv|Environ)\s*\(", "os.Getenv"),
    ],
    "serialization": [
        ("java", r"\bnew\s+(?:ObjectInputStream|XMLDecoder)\s*\(", "java deserialization"), ("java", r"\.readObject\s*\(", "readObject"),
        ("csharp", r"\b(?:BinaryFormatter|NetDataContractSerializer|LosFormatter|SoapFormatter|JavaScriptSerializer)\b", "unsafe .NET serializer"),
        ("javascript", r"\b(?:node-serialize|serialize-javascript)\b|\bunserialize\s*\(", "js deserialization"),
        ("go", r"\bgob\.NewDecoder\s*\(", "encoding/gob"),
    ],
    "obfuscation": [
        ("java", r"\bBase64\.get(?:Mime)?Decoder\(\)\s*\.decode\s*\(", "Base64.decode"),
        ("csharp", r"\bConvert\.FromBase64String\s*\(", "Convert.FromBase64String"),
        ("javascript", r"(?<![\w.])atob\s*\(|\bBuffer\.from\s*\([^)]*['\"]base64['\"]", "base64 decode"),
        ("javascript", r"\bString\.fromCharCode\s*\((?:\s*\d+\s*,){5,}", "String.fromCharCode"),
        ("go", r"\bbase64\.(?:Std|URL|RawStd|RawURL)Encoding\.DecodeString\s*\(", "base64.DecodeString"),
    ],
}
_LANG_OF = {"typescript": "javascript"}            # TS shares the JS patterns
_PATTERNS = {lang: [(cap, re.compile(rx), api) for cap, rules in _C_FAMILY.items() for (lng, rx, api) in rules if lng == lang]
             for lang in {"java", "csharp", "javascript", "go"}}
_B64_LITERAL = re.compile(r"[\"'`]([A-Za-z0-9+/]{%d,}={0,2})[\"'`]" % _MIN_BASE64)


def strip_comments(text: str) -> str:
    """``//`` and ``/* */`` comments removed (strings kept, so a ``//`` in a URL survives), newlines preserved."""
    out, i, n, quote = [], 0, len(text), None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            seg = text[i:n if j < 0 else j + 2]
            out.append("\n" * seg.count("\n"))
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def extract_file(path: Path, root: str = "") -> list[dict[str, Any]]:
    lang = LANGUAGES.get(path.suffix.lower())
    if lang is None:
        return []
    try:
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []
    rel = str(path.relative_to(root)) if root else str(path)
    starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line(pos: int) -> int:
        lo, hi = 0, len(starts)
        while lo < hi:
            mid = (lo + hi) // 2
            if starts[mid] <= pos:
                lo = mid + 1
            else:
                hi = mid
        return lo
    recs = []
    for cap, rx, api in _PATTERNS[_LANG_OF.get(lang, lang)]:
        for m in rx.finditer(text):
            ln = line(m.start())
            recs.append({"file_path": rel, "function_name": "", "capability_type": cap,
                         "evidence": {"api": api, "language": lang, "method": "lexical", "match": m.group(0)[:80]},
                         "line_start": ln, "line_end": ln, "risk_weight": RISK_WEIGHTS_CAPABILITY.get(cap, 0.0)})
    for m in _B64_LITERAL.finditer(text):
        ln = line(m.start())
        recs.append({"file_path": rel, "function_name": "", "capability_type": "obfuscation",
                     "evidence": {"api": "base64 literal", "language": lang, "method": "lexical", "length": len(m.group(1))},
                     "line_start": ln, "line_end": ln, "risk_weight": RISK_WEIGHTS_CAPABILITY.get("obfuscation", 0.0)})
    return sorted(recs, key=lambda r: (r["file_path"], r["line_start"], r["capability_type"]))


def _walk(path: Path):
    if path.is_file():
        yield path
        return
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path).parts
        if p.is_file() and not any(part in _SKIP or part.startswith(".") for part in rel[:-1]):
            yield p


def extract(source: str) -> list[dict[str, Any]]:
    """Capability records for every Java, C#, JavaScript/TypeScript and Go file under ``source``."""
    path = Path(source)
    if not path.exists():
        return []
    root = "" if path.is_file() else str(path)
    return [r for p in _walk(path) for r in extract_file(p, root)]


def coverage(source: str) -> dict[str, Any]:
    """Which source files SIPA read (``python`` by AST, the rest lexically) and which it could NOT read."""
    path = Path(source)
    assessed: dict[str, int] = {}
    unassessed: dict[str, int] = {}
    if path.exists():
        for p in _walk(path):
            ext = p.suffix.lower()
            if ext == ".py":
                assessed["python"] = assessed.get("python", 0) + 1
            elif ext in LANGUAGES:
                assessed[LANGUAGES[ext]] = assessed.get(LANGUAGES[ext], 0) + 1
            elif ext in UNREAD:
                unassessed[ext] = unassessed.get(ext, 0) + 1
    return {"assessed": dict(sorted(assessed.items())), "unassessed": dict(sorted(unassessed.items())),
            "methods": {"python": "ast", **{v: "lexical" for v in sorted(set(LANGUAGES.values()))}}}
