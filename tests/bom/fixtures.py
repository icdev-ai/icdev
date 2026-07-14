# CUI // SP-CTI
"""Documents that misbehave in the specific ways real ones do.

These are BUILT, not checked in. Two reasons, and the second is the important
one.

First, hermetic: they run in CI, where no customer corpus exists and none should.

Second, and this is the whole point of the module: the failure modes this engine
hunts are *structural*. A formula multiplying a quantity by an empty cell yields
zero whether the item is a network switch or a marine gearbox. A worksheet that
reports A1:A1 can still be carrying an anchored image, in any workbook anyone has
ever made. A gap in the sheetId sequence means a sheet was deleted, always.

So the fixtures reproduce the *shapes* of the bugs, with invented content. An
engine that passes against these will find the same defects in documents nobody
here has ever seen — which is the actual requirement, and is a stronger claim
than "it works on the one corpus we had."
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def _inject_cached_values(path: Path, values: dict[str, float]) -> None:
    """Give formula cells the value Excel would have cached.

    openpyxl never RUNS a formula, so a workbook it writes has ``<f>`` and no
    ``<v>``. Load it with ``data_only=True`` and every formula cell comes back
    None. A workbook a human saved from Excel carries both.

    Both exist in the wild and the engine has to survive both — a script-written
    estimate is exactly the kind whose arithmetic most wants checking. So the
    fixtures make one of each rather than quietly only testing the easy one.
    """
    import re

    with zipfile.ZipFile(path) as src:
        items = [(i, src.read(i.filename)) for i in src.infolist()]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info, blob in items:
            if info.filename.startswith("xl/worksheets/sheet"):
                text = blob.decode("utf-8")
                for ref, val in values.items():
                    # openpyxl emits an EMPTY <v></v> after the formula, so the
                    # cell already has the slot — it is just never filled, because
                    # nothing ever evaluated the formula.
                    #   <c r="D2"><f>B2*C2</f><v></v></c>
                    #     -> <c r="D2"><f>B2*C2</f><v>200</v></c>
                    text = re.sub(
                        rf'(<c r="{ref}"[^>]*>\s*<f>[^<]*</f>\s*)(?:<v>\s*</v>)?\s*</c>',
                        rf"\1<v>{val}</v></c>",
                        text,
                    )
                blob = text.encode("utf-8")
            dst.writestr(info, blob)


def workbook_with_a_zeroed_line(path: Path, *, cached: bool = True) -> Path:
    """A quantity, no unit price, and a formula that politely multiplies to zero.

    The line looks costed. It contributes nothing. The total is understated by
    whatever that item is worth, and the spreadsheet will never say so.

    ``cached=True`` mimics a workbook saved from Excel (formula AND the value it
    produced). ``cached=False`` mimics one a script wrote (formula, no value) —
    where the zero is not even visible, and the only evidence is a formula
    multiplying by a cell that does not exist.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["Item", "Qty", "Unit", "Extended"])

    ws.append(["Widget A", 2, 100, None])
    ws["D2"] = "=B2*C2"                      # 200 — a normal, healthy line

    ws.append(["Widget B", 1, None, None])   # unit price never entered
    ws["D3"] = "=B3*C3"                      # 0 — and nobody notices

    ws.append(["TOTAL", None, None, None])
    ws["D4"] = "=SUM(D2:D3)"

    wb.save(path)

    if cached:
        _inject_cached_values(path, {"D2": 200, "D3": 0, "D4": 200})
    return path


def workbook_with_a_deleted_sheet(path: Path) -> Path:
    """sheetIds 1,2,4 — sheet 3 is gone.

    Excel never reuses a sheetId, so a hole in the run is a deletion. In a
    costing workbook that is a category somebody removed, and the surviving
    subtotals will not tell you whether its money went with it.

    openpyxl always writes a contiguous sequence, so the gap is punched into the
    saved package directly.
    """
    import openpyxl

    tmp = path.with_suffix(".seed.xlsx")
    wb = openpyxl.Workbook()
    wb.active.title = "Alpha"
    wb.create_sheet("Beta")
    wb.create_sheet("Gamma")
    wb.save(tmp)

    with zipfile.ZipFile(tmp) as src, zipfile.ZipFile(path, "w") as dst:
        for item in src.infolist():
            blob = src.read(item.filename)
            if item.filename == "xl/workbook.xml":
                text = blob.decode("utf-8")
                text = text.replace('sheetId="3"', 'sheetId="4"')
                blob = text.encode("utf-8")
            dst.writestr(item, blob)

    tmp.unlink()
    return path


def workbook_with_an_image_on_an_empty_sheet(path: Path, message: str) -> Path:
    """A worksheet with zero cells, carrying a picture.

    Excel reports the sheet as A1:A1. It opens blank. Nobody scrolling the
    workbook finds it. The picture is where somebody pasted a screenshot of the
    thing that actually constrains the project.
    """
    import openpyxl
    from openpyxl.drawing.image import Image as XLImage
    from PIL import Image, ImageDraw

    png = path.with_suffix(".png")
    img = Image.new("RGB", (760, 180), "white")
    draw = ImageDraw.Draw(img)
    # Large and plain: this has to survive OCR, and the test asserts on what
    # comes back out of the pixels.
    for i, line in enumerate(message.split("\n")):
        draw.text((14, 20 + i * 46), line, fill="black")
    img = img.resize((1520, 360), Image.LANCZOS)
    img.save(png)

    wb = openpyxl.Workbook()
    wb.active.title = "Data"
    wb["Data"]["A1"] = "something"
    blank = wb.create_sheet("Notes")     # not one cell written to it
    blank.add_image(XLImage(str(png)), "A1")
    wb.save(path)

    png.unlink(missing_ok=True)
    return path


def deck_with_a_table_that_is_not_a_table(path: Path) -> Path:
    """A grid drawn out of loose text boxes.

    ``shape.has_table`` is False for every one of them. This is what a
    script-generated deck looks like, and what you get when a human builds a
    table by duplicating a text box — so an extractor that only understands real
    tables reads the money slide as scattered words and loses the total in
    silence.
    """
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])   # blank

    rows = [
        ("Category", "Amount"),
        ("Alpha", "$1,000"),
        ("Beta", "$2,500"),
        ("Total", "$3,500"),
    ]
    for r, (left_text, right_text) in enumerate(rows):
        # A couple of EMU of vertical jitter, because real decks are never
        # pixel-aligned and a clusterer that demands exactness is useless.
        jitter = 900 * (r % 2)
        for c, text in enumerate((left_text, right_text)):
            box = slide.shapes.add_textbox(
                Emu(500_000 + c * 3_000_000),
                Emu(500_000 + r * 800_000 + jitter),
                Emu(2_800_000),
                Emu(600_000),
            )
            box.text_frame.text = text

    notes = slide.notes_slide.notes_text_frame
    notes.text = (
        "The long-lead item gates everything behind it. Order in week one or the "
        "schedule does not hold. Unit price for line 2 is still TBD."
    )
    prs.save(path)
    return path


def drawio_with_tabs(path: Path) -> Path:
    """A real .drawio file: <mxfile> wrapping one <diagram> per tab.

    The rack elevation is the only tab that says HOW MANY, so the tabs have to
    stay apart. It also draws more units than the inventory will turn out to
    verify — which is a question for a human, not a conclusion for a tool.
    """
    workers = "".join(
        f'<mxCell id="w{i}" value="NODE-X — Worker #{i}" style="rounded=0;" '
        f'vertex="1" parent="1"/>'
        for i in range(1, 13)
    )
    xml = f"""<mxfile host="test">
  <diagram name="Floor Plan" id="d1"><mxGraphModel><root>
    <mxCell id="0"/><mxCell id="1" parent="0"/>
    <mxCell id="f1" value="Room A" style="rounded=0;" vertex="1" parent="1"/>
  </root></mxGraphModel></diagram>
  <diagram name="Rack Elevation" id="d2"><mxGraphModel><root>
    <mxCell id="0"/><mxCell id="1" parent="0"/>
    {workers}
  </root></mxGraphModel></diagram>
</mxfile>"""
    path.write_text(xml, encoding="utf-8")
    return path


def copy_of(src: Path, dst: Path) -> Path:
    shutil.copy2(src, dst)
    return dst
