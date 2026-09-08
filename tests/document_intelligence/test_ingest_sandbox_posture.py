# CUI // SP-CTI
"""Gap 69 — the DIC ingest posture is real, and the decision is checkable (dwr-fid-03).

``docs/security/sandbox-coverage.md`` carried no decision for
``POST /document-intelligence/api/ingest`` — the widest ingress in the tree: an
arbitrary uploaded file, no extension allowlist, no per-route size cap, handed
to pypdf / pymupdf / pdfplumber / pypdfium2 / python-docx / openpyxl /
python-pptx / Pillow / easyocr / pytesseract, and to MarkItDown's archive
expansion where it is installed. Gap 23 (`.pptx`), Gap 24 (BI dataset) and Gap
14 (NMCE config) all had one; this did not, and the ``sandbox_coverage``
coherence check asserts only that the document exists and is linked, so nothing
would ever have caught it.

These pin the two things a taxonomy decision rests on:

  * the FACTS the rationale claims — no exec/eval/shell in the extraction path,
    and a client filename that cannot put a path component on disk;
  * that ``sandboxed-on-demand`` is CONSUMED rather than declared. A posture
    nothing reads is the declared-but-never-consumed defect one layer up from
    the code it purports to govern.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

DOC = pathlib.Path("docs/security/sandbox-coverage.md")
GUARD = pathlib.Path("tools/document_intelligence/ingest_guard.py")

# Spelled as literals, NOT imported, so this module still IMPORTS against a tree
# that has no ingest_guard -- the red-first proof is then "the posture is not
# enforced" rather than a collection error proving only that a file was added.
CLASS_TEXT = "text"
CLASS_HTML = "html"
CLASS_NATIVE = "native"


def _guard():
    from tools.document_intelligence import ingest_guard

    return ingest_guard


# ── the decision exists and says what it decided ─────────────────────────────

def test_the_ingest_route_has_a_decision():
    body = DOC.read_text(encoding="utf-8", errors="replace")
    assert "/document-intelligence/api/ingest" in body, \
        "sandbox-coverage.md carries no entry for the DIC ingest route"
    gap = body[body.index("### Gap 69"):]
    assert "**sandboxed-on-demand**" in gap, "Gap 69 states no taxonomy decision"


def test_the_decision_names_the_two_absences_it_is_wider_for():
    """A decision that did not record "no allowlist, no per-route cap" would be
    indistinguishable from Gap 23's, which has both."""
    body = DOC.read_text(encoding="utf-8", errors="replace")
    gap = body[body.index("### Gap 69"):]
    assert "no extension allowlist" in gap
    assert "no per-route size cap" in gap
    assert "Residual" in gap, "Gap 69 claims no residual — it has two"


def test_the_decision_does_not_claim_isolation_it_does_not_have():
    """DIC extraction is NOT routed through SandboxExecutor. An entry that read
    as though it were would be the exact defect the on-demand tier invites."""
    body = DOC.read_text(encoding="utf-8", errors="replace")
    gap = body[body.index("### Gap 69"):]
    assert "refusal, not isolation" in gap


# ── the posture is CONSUMED, not merely declared ─────────────────────────────

def test_a_strict_host_refuses_a_native_parser_format():
    g = _guard()
    for name in ("report.pdf", "spec.docx", "book.xlsx", "deck.pptx", "scan.png", "bundle.zip"):
        v = g.evaluate_upload(name, strict=True)
        assert v["allowed"] is False, f"{name} was allowed on a strict host"
        assert "ICDEV_STRICT_SANDBOX" in v["reason"]
        assert v["posture"] == "strict"


def test_a_strict_host_still_takes_plain_text():
    g = _guard()
    for name in ("notes.txt", "policy.md", "data.csv", "conf.yaml"):
        v = g.evaluate_upload(name, strict=True)
        assert v["allowed"] is True, f"{name} was refused although it is a stdlib decode"
        assert v["parser_class"] == CLASS_TEXT


def test_a_permissive_host_is_unchanged():
    """ICDEV_STRICT_SANDBOX is unset everywhere this ships, so the guard owes no
    fire-rate survey: it refuses nothing by default."""
    g = _guard()
    for name in ("report.pdf", "scan.png", "bundle.zip", "notes.txt", "no_extension"):
        v = g.evaluate_upload(name, strict=False)
        assert v["allowed"] is True, f"{name} refused on a permissive host"
        assert v["posture"] == "permissive"


def test_the_route_consumes_the_guard():
    """A guard nothing calls is the declared-but-never-consumed defect. Read
    from the AST so a future edit that deletes the call is caught even if the
    import survives."""
    src = pathlib.Path("tools/document_intelligence/blueprint.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "api_ingest"), None
    )
    assert fn is not None, "api_ingest is gone — re-point this test"
    called = {
        n.func.id for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "evaluate_upload" in called, "api_ingest does not consult the ingest guard"


def test_the_guard_reads_the_shared_strict_switch():
    """A second reading of ICDEV_STRICT_SANDBOX is how one surface starts
    disagreeing with another about whether a host is strict."""
    src = GUARD.read_text(encoding="utf-8")
    assert "from tools.analyzers.sandbox import strict_sandbox_enabled" in src
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "strict_sandbox_enabled":
            raise AssertionError("ingest_guard re-implements the strict switch")
    # and it never reads the env var itself
    assert "ICDEV_STRICT_SANDBOX" not in src.replace(
        "``ICDEV_STRICT_SANDBOX``", ""
    ).replace("ICDEV_STRICT_SANDBOX=1", ""), \
        "ingest_guard reads ICDEV_STRICT_SANDBOX directly instead of asking the shared switch"


# ── the classification is derived, not respelled ─────────────────────────────

def test_the_permitted_class_follows_the_extractor_registry():
    """An extension is `text` only because the registry maps it to
    ``_extract_text``. A second hand-kept list is how a new binary format joins
    the permitted class by being forgotten."""
    g = _guard()
    from tools.document_intelligence import extractors

    for ext, fn in extractors._EXTRACTORS.items():
        klass = g.parser_class(ext)
        if fn is extractors._extract_text:
            assert klass == CLASS_TEXT, f"{ext} maps to _extract_text but classes as {klass}"
        elif fn is extractors._extract_html:
            assert klass == CLASS_HTML, f"{ext} maps to _extract_html but classes as {klass}"
        else:
            assert klass == CLASS_NATIVE, f"{ext} reaches {fn.__name__} but classes as {klass}"


def test_a_registry_format_can_never_be_forgotten_into_the_permitted_class():
    """The property the derivation buys, asserted directly: every non-text
    extractor in the registry is refused on a strict host."""
    g = _guard()
    from tools.document_intelligence import extractors

    for ext, fn in extractors._EXTRACTORS.items():
        if fn is extractors._extract_text:
            continue
        assert g.evaluate_upload(f"f{ext}", strict=True)["allowed"] is False, \
            f"{ext} reaches {fn.__name__} and was allowed on a strict host"


def test_an_unknown_extension_is_classified_by_its_code_path():
    """extract_file's fallback is Path.read_text, so a .pdf renamed .foo is
    decoded as text and never reaches a parser."""
    g = _guard()
    assert g.parser_class(".foo") == CLASS_TEXT
    assert g.parser_class("") == CLASS_TEXT
    assert g.evaluate_upload("mystery.foo", strict=True)["allowed"] is True


def test_markitdown_only_formats_are_classed_on_their_worst_case():
    """markitdown is not installed here. Classing .zip on what happens to be
    installed would silently permit archive expansion on a host that has it."""
    g = _guard()
    for ext in (".zip", ".msg", ".eml", ".epub", ".xls"):
        assert g.parser_class(ext) == CLASS_NATIVE, f"{ext} is not classed native"


# ── the facts the rationale rests on ─────────────────────────────────────────

def test_the_extraction_path_has_no_exec_eval_or_shell():
    """The rationale's central claim. `subprocess` appears exactly once in
    extractors.py — yt-dlp on the URL/video path, a DIFFERENT route — so the
    assertion is scoped to the file-upload dispatch and names the exception."""
    src = pathlib.Path("tools/document_intelligence/extractors.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    url_only = {"_extract_via_ytdlp", "extract_video", "extract_youtube", "extract_url"}
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name in url_only:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and \
                    sub.func.id in {"exec", "eval", "compile", "__import__"}:
                offenders.append(f"{node.name}:{sub.func.id}")
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in sub.names] + (
                    [sub.module] if isinstance(sub, ast.ImportFrom) else []
                )
                if any(n and n.split(".")[0] == "subprocess" for n in names):
                    offenders.append(f"{node.name}:imports {names}")
    assert not offenders, f"the file-upload extraction path grew a code-execution surface: {offenders}"


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "a.pdf/../../evil",
        r"x.\..\y",
        "..\\..\\windows\\system32\\cmd.exe",
        "/absolute/path.pdf",
    ],
)
def test_a_client_filename_cannot_put_a_path_component_on_disk(name):
    """The only part of the client's filename that reaches the filesystem is
    ``Path(filename).suffix`` — as the temp file's suffix and as the retained
    original's. ``Path`` treats every separator as a boundary, so a suffix can
    never carry one."""
    from pathlib import Path as _P

    suffix = _P(name).suffix.lower()
    assert "/" not in suffix and "\\" not in suffix
    assert ".." not in suffix


def test_the_retained_original_lands_at_a_content_address_not_a_client_path(tmp_path):
    from tools.document_intelligence import originals

    src = tmp_path / "upload.pdf"
    src.write_bytes(b"%PDF-1.4\nhello\n")
    root = tmp_path / "store"
    kept = originals.retain_original(str(src), "../../../etc/passwd.pdf", root=root)

    dest = pathlib.Path(kept.path).resolve()
    assert root.resolve() in dest.parents, f"retained original escaped the store: {dest}"
    assert kept.sha256 in dest.name
    assert "passwd" not in dest.name
