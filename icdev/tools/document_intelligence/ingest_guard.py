# CUI // SP-CTI
"""The `sandboxed-on-demand` posture for DIC file ingest, made REAL (dwr-fid-03).

``POST /document-intelligence/api/ingest`` accepts an arbitrary uploaded file
with NO extension allowlist and NO per-route size cap, and hands it to
``extract_file`` — pypdf / pymupdf / pdfplumber / pypdfium2 / python-docx /
openpyxl / python-pptx / Pillow / easyocr / pytesseract, and optionally
MarkItDown, which additionally expands ``.zip``, ``.msg``/``.eml`` and audio.
Gap 69 in ``docs/security/sandbox-coverage.md`` records the decision for that
ingress; this module is what makes the decision more than a sentence.

WHY A REFUSAL AND NOT ISOLATION. ``sandboxed-on-demand`` in the taxonomy means
"the sandbox activates only under ``ICDEV_STRICT_SANDBOX=1``". Declaring that
posture while nothing consults the flag is the declared-but-never-consumed
defect this platform ships most: a declaration wearing the name of a control.
Routing DIC extraction through ``SandboxExecutor`` needs a container image
carrying the platform (``tools/analyzers/sandbox.py`` states that requirement
for the analyzer path) and is a larger change than this card; what CAN be made
true today is the fail-closed half. On a strict host the native-parser formats
are REFUSED at the door with a reason, rather than parsed in-process behind a
flag that claims they are not. That is `tools/analyzers/sandbox.py`'s own
doctrine — *"Refusing loudly is the point"* — and it is stated as a refusal
everywhere it is reported, never as isolation.

THE SWITCH IS THE SHARED ONE. ``strict_sandbox_enabled`` is IMPORTED from
``tools.analyzers.sandbox``; a second reading of ``ICDEV_STRICT_SANDBOX`` here
is how one surface starts disagreeing with another about whether a host is
strict. Pinned by test.

THE CLASSES ARE DERIVED FROM THE EXTRACTOR REGISTRY, NEVER RESPELLED. An
extension's class is decided by WHICH FUNCTION ``extractors._EXTRACTORS`` maps
it to, so a format added to that registry cannot silently join the permitted
class by being forgotten here:

    text    ``_extract_text`` — ``Path.read_text``, stdlib and nothing else.
            The UNKNOWN-extension fallback lands here too, and correctly: a
            ``.pdf`` renamed ``.foo`` is read as bytes-to-text and never
            reaches a parser. The classification is of the CODE PATH.
    html    ``_extract_html`` — delegates to ``tools/http/page_extract.py``,
            which is Gap 37 and has its own decision. Refused under strict all
            the same: a posture this module has no decision for must not be
            handed the most permissive outcome.
    native  everything else — a third-party or native parser, or OCR.

DEFAULT IS UNCHANGED. ``ICDEV_STRICT_SANDBOX`` is unset on every deployment
this ships to, so :func:`evaluate_upload` allows every upload it allowed
before and no fire-rate question arises. Turning it on is an IL5 / air-gap
deployment decision that trades DIC's ability to ingest binary formats for the
guarantee that no native parser sees an upload unisolated.

NOT ENFORCED HERE, and named rather than implied:
  * no per-route size cap — the only limit is the platform-wide
    ``MAX_CONTENT_LENGTH`` (``ICDEV_MAX_UPLOAD_MB``, 50 MB). Reported by
    :func:`evaluate_upload` as ``size_cap`` so a caller can see which limit
    applies rather than assume one exists.
  * the URL and YouTube ingress (``/api/ingest/url``, ``/api/ingest/youtube``)
    are DIFFERENT routes with a different surface — ``extract_video`` spawns
    ``yt-dlp``. Out of scope here and named in Gap 69.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from tools.analyzers.sandbox import strict_sandbox_enabled

#: Stdlib text decode only. Permitted on a strict host.
CLASS_TEXT = "text"
#: The governed HTML content filter (Gap 37). Refused on a strict host.
CLASS_HTML = "html"
#: A third-party / native parser or OCR. Refused on a strict host.
CLASS_NATIVE = "native"

PARSER_CLASSES: tuple[str, ...] = (CLASS_TEXT, CLASS_HTML, CLASS_NATIVE)

#: The classes a strict host will run in-process. Everything else is refused.
STRICT_ALLOWED_CLASSES: frozenset[str] = frozenset({CLASS_TEXT})

#: Formats MarkItDown claims that the built-in registry does not — an ARCHIVE,
#: mail containers and audio. They reach a native parser when MarkItDown is
#: installed and the plain fallback otherwise, so they are classified `native`
#: on their worst case rather than on what happens to be installed here.
_MARKITDOWN_ONLY: frozenset[str] = frozenset({
    ".zip", ".msg", ".eml", ".epub", ".xls", ".mp3", ".wav", ".m4a", ".webp",
})



def safe_suffix(filename: str | None) -> str:
    r"""The client filename's extension, with NO path semantics from either host.

    `Path(name).suffix` is HOST-DEPENDENT and that is a security property here,
    not a style question. `pathlib.Path` only treats a separator as a boundary if
    the host uses it, so:

        PurePosixPath(r"x.\..\y").suffix    == ".\y"    (backslash survives on Linux)
        PureWindowsPath("a.pdf/../evil").suffix == ""     (Windows splits on both)

    Each platform therefore lets the OTHER platform's separator through, and this
    value is handed straight to `tempfile.NamedTemporaryFile(suffix=...)` and to
    the retained original's name. Measured 2026-09-08: the sandbox-posture test
    asserted the Windows behaviour and passed locally while failing on the Linux
    runner, which is where this actually runs.

    So the last component is taken under BOTH conventions, and the result must
    then look like an extension -- a dot and a short alphanumeric run. Anything
    else yields "", because a suffix nobody can vouch for is not worth carrying
    into a filename.
    """
    import re as _re

    name = _re.split(r"[\/]", str(filename or ""))[-1]
    suffix = PurePosixPath(name).suffix.lower()
    return suffix if _re.fullmatch(r"\.[a-z0-9]{1,12}", suffix or "") else ""


def _registry() -> dict[str, Any]:
    from tools.document_intelligence import extractors

    return dict(getattr(extractors, "_EXTRACTORS", {}))


def parser_class(extension: str) -> str:
    """Which parser class this extension reaches. Derived, never a literal list.

    An extension the registry does not know reaches ``extract_file``'s utf-8
    fallback, which is the same stdlib decode as :data:`CLASS_TEXT` — UNLESS
    MarkItDown claims it, which is checked first and may hand it a real parser.
    """
    ext = (extension or "").lower()
    if ext in _MARKITDOWN_ONLY:
        return CLASS_NATIVE
    from tools.document_intelligence import extractors

    fn = _registry().get(ext)
    if fn is None:
        return CLASS_TEXT  # the unknown-extension utf-8 fallback
    if fn is getattr(extractors, "_extract_text", None):
        return CLASS_TEXT
    if fn is getattr(extractors, "_extract_html", None):
        return CLASS_HTML
    return CLASS_NATIVE


def platform_size_cap_bytes() -> Optional[int]:
    """The only size limit an upload to this route meets, or ``None``.

    Read from the same ``ICDEV_MAX_UPLOAD_MB`` the dashboard turns into
    ``MAX_CONTENT_LENGTH``. Reported so a caller can state which limit applies
    instead of assuming a per-route one exists — there is none.
    """
    try:
        mb = float(os.environ.get("ICDEV_MAX_UPLOAD_MB", "50"))
    except (TypeError, ValueError):
        mb = 50.0
    return int(mb * 1024 * 1024) if mb > 0 else None


def evaluate_upload(
    filename: str,
    *,
    content_length: Optional[int] = None,
    strict: Optional[bool] = None,
) -> dict[str, Any]:
    """Should this upload be extracted on THIS host?

    Args:
        filename: the client-supplied name. Only its suffix is read.
        content_length: the request's declared size, when the caller has it.
        strict: override the ``ICDEV_STRICT_SANDBOX`` reading. ``None`` asks
            :func:`tools.analyzers.sandbox.strict_sandbox_enabled`.

    Returns a verdict that always names its posture, so a permitted upload on a
    permissive host is distinguishable from one nothing ever assessed::

        {"allowed": bool, "reason": str, "posture": "permissive"|"strict",
         "extension": str, "parser_class": str, "size_cap": int|None,
         "size_cap_scope": "platform"}
    """
    ext = Path(filename or "").suffix.lower()
    klass = parser_class(ext)
    is_strict = strict_sandbox_enabled() if strict is None else bool(strict)
    cap = platform_size_cap_bytes()
    verdict: dict[str, Any] = {
        "allowed": True,
        "reason": "permissive_host",
        "posture": "strict" if is_strict else "permissive",
        "extension": ext,
        "parser_class": klass,
        "size_cap": cap,
        "size_cap_scope": "platform",
        "content_length": content_length,
    }
    if not is_strict:
        return verdict
    if klass in STRICT_ALLOWED_CLASSES:
        verdict["reason"] = "strict_host_text_only"
        return verdict
    verdict["allowed"] = False
    verdict["reason"] = (
        f"ICDEV_STRICT_SANDBOX=1: '{ext or 'no extension'}' reaches a "
        f"{klass} parser, and DIC extraction is not routed through "
        "SandboxExecutor. Refused rather than parsed unisolated. Ingest it on a "
        "permissive host, or convert it to text first."
    )
    return verdict
