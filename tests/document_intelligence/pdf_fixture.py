# CUI // SP-CTI
"""A minimal, dependency-free PDF writer for the dwr-fid-02 geometry tests.

There is no declared PDF *writer* in ``requirements.txt`` — pdfplumber and
pypdf read, python-docx writes only DOCX — so a test that needs a PDF either
checks a binary into the tree or builds one. This builds one: ~40 lines of the
format's own syntax, with a correct xref table, so the suite has no fixture
file to keep in step and no undeclared dependency to install.

Deliberately NOT reportlab or fpdf2: adding a dependency so a test can make its
input is exactly the trade this card refuses elsewhere (the pymupdf rule).
"""
from __future__ import annotations


def make_pdf(pages: list[list[tuple[str, float, float]]], width: float = 612.0,
             height: float = 792.0, font_size: float = 12.0) -> bytes:
    """A PDF with one content stream per page.

    ``pages`` is a list of pages, each a list of ``(text, x, y_from_bottom)``
    placements in PDF points. ``y`` is from the page BOTTOM because that is the
    PDF's own origin — pdfplumber is what converts it to ``top``, and a fixture
    that pre-converted would be testing our arithmetic against itself.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)  # 1-based object number

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: list[int] = []
    content_ids: list[int] = []
    for placements in pages:
        parts = [b"BT", f"/F1 {font_size} Tf".encode("ascii")]
        for text, x, y in placements:
            escaped = (
                text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            )
            parts.append(f"1 0 0 1 {x} {y} Tm ({escaped}) Tj".encode("ascii"))
        parts.append(b"ET")
        stream = b"\n".join(parts)
        content_ids.append(
            add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        )
        page_ids.append(0)  # placeholder, filled once /Pages exists

    pages_id = len(objects) + len(pages) + 1
    for i, content_id in enumerate(content_ids):
        page_ids[i] = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (
                pages_id,
                f"{width}".encode("ascii"),
                f"{height}".encode("ascii"),
                font_id,
                content_id,
            )
        )
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))
    )
    assert actual_pages_id == pages_id, "page-tree object number was mispredicted"
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number
        out += body
        out += b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        xref_at,
    )
    return bytes(out)
