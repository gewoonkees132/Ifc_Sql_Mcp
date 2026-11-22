"""Build docs/roadmap/feature-request.pdf, a printable and fillable feature request.

0. The system's Inter is OpenType with CFF outlines, which Chromium can only embed as
   Type 3 fonts (blurry in some viewers, poor text selection). fontTools converts each
   weight to TrueType outlines once, into ``.fonts/`` (git-ignored).
1. ``render.mjs`` prints ``feature_request.html`` with Chromium and records where each
   form box (class ``ff``) sits on its page.
2. reportlab draws a fillable field over every box: one radio group per vote, text
   fields for notes and details, a checkbox for the audit session.
3. pypdf puts the printed pages under the fields, gives the buttons a clean appearance
   and writes the PDF.

Run from anywhere (one command)::

    uv run --no-project --with fonttools --with reportlab --with pypdf
        python docs/roadmap/source/build.py

It needs Node with Playwright's Chromium, Inter in ``INTER_DIR`` and JetBrains Mono at
the path in feature_request.html.
"""

# ruff: noqa: T201

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    TextStringObject,
)
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "feature-request.pdf"
INTER_DIR = Path("/usr/share/fonts/opentype/inter")
INTER_WEIGHTS = ("Light", "Regular", "Italic", "Medium", "SemiBold", "Bold")
FONT_DIR = HERE / ".fonts"

RED = Color(0xD0 / 255, 0x26 / 255, 0x1B / 255)
INK = Color(0x16 / 255, 0x16 / 255, 0x16 / 255)
PAGE_H = A4[1]
INSET = 1.2  # pt kept clear of the printed border
KAPPA = 0.5523  # control-point distance of a quarter circle drawn as one Bézier curve


def _otf_to_ttf(src: Path, dst: Path) -> None:
    """Convert an OpenType font with CFF outlines to TrueType (quadratic) outlines.

    The fontTools otf2ttf recipe: each cubic outline becomes quadratic within 1 unit.
    """
    font = TTFont(src)
    if font.sfntVersion != "OTTO" or "CFF " not in font:
        raise ValueError(f"{src} has no CFF outlines")
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    glyphs = {}
    for name in glyph_set:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, 1.0, reverse_direction=True))
        glyphs[name] = pen.glyph()
    font["loca"] = newTable("loca")
    font["glyf"] = glyf = newTable("glyf")
    glyf.glyphOrder = order
    glyf.glyphs = glyphs
    del font["CFF "]
    if "VORG" in font:
        del font["VORG"]
    glyf.compile(font)
    hmtx = font["hmtx"]
    for name, glyph in glyf.glyphs.items():
        if hasattr(glyph, "xMin"):
            hmtx[name] = (hmtx[name][0], glyph.xMin)
    font["maxp"] = maxp = newTable("maxp")
    maxp.tableVersion = 0x00010000
    maxp.maxZones = 1
    for attr in (
        "maxTwilightPoints",
        "maxStorage",
        "maxFunctionDefs",
        "maxInstructionDefs",
        "maxStackElements",
        "maxSizeOfInstructions",
    ):
        setattr(maxp, attr, 0)
    maxp.maxComponentElements = max(
        (len(getattr(g, "components", None) or []) for g in glyphs.values()), default=0
    )
    maxp.compile(font)
    post = font["post"]
    post.formatType, post.extraNames, post.mapping, post.glyphOrder = 2.0, [], {}, order
    font.sfntVersion = "\x00\x01\x00\x00"
    font.save(dst)


def _truetype_inter() -> None:
    """Convert each Inter weight the page uses, unless it was converted before."""
    FONT_DIR.mkdir(exist_ok=True)
    for weight in INTER_WEIGHTS:
        dst = FONT_DIR / f"Inter-{weight}.ttf"
        if not dst.exists():
            _otf_to_ttf(INTER_DIR / f"Inter-{weight}.otf", dst)
            print(f"converted Inter {weight} to TrueType outlines")


def _fields_pdf(report: dict[str, Any], path: Path) -> None:
    """Write a PDF with one page per printed page, holding only the form fields."""
    c = canvas.Canvas(str(path), pagesize=A4)
    by_page: dict[int, list[dict[str, Any]]] = {}
    for f in report["fields"]:
        by_page.setdefault(f["page"], []).append(f)
    form = c.acroForm
    for page in range(report["pages"]):
        for f in by_page.get(page, []):
            x = f["x"]
            y = PAGE_H - f["top"] - f["h"]
            kind = f["kind"]
            if kind in ("radio", "check"):
                common = {
                    "x": x + INSET,
                    "y": y + INSET,
                    "size": min(f["w"], f["h"]) - 2 * INSET,
                    "buttonStyle": "check",
                    "borderWidth": 0,
                    "borderColor": RED,
                    "fillColor": None,
                    "textColor": RED,
                    "forceBorder": False,
                }
                if kind == "radio":
                    form.radio(
                        name=f["name"],
                        value=f["value"],
                        selected=False,
                        tooltip=f"{f['name']}: {f['value']}",
                        **common,
                    )
                else:
                    form.checkbox(name=f["name"], checked=False, tooltip=f["name"], **common)
            elif kind in ("text", "textml"):
                multiline = kind == "textml"
                form.textfield(
                    name=f["name"],
                    tooltip=f["name"],
                    x=x + INSET,
                    y=y + INSET,
                    width=f["w"] - 2 * INSET,
                    height=f["h"] - 2 * INSET,
                    borderWidth=0,
                    fillColor=None,
                    textColor=INK,
                    fontName="Helvetica",
                    fontSize=9 if multiline else 10,
                    fieldFlags="multiline" if multiline else "",
                )
            else:
                raise ValueError(f"unknown field kind {kind!r} for {f['name']}")
        c.showPage()
    c.save()


def _form_xobject(writer: PdfWriter, w: float, h: float, ops: str) -> IndirectObject:
    """Add a form XObject, w wide and h high, drawing ``ops``: a button appearance."""
    stream = DecodedStreamObject()
    stream.set_data(ops.encode("ascii"))
    stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (0, 0, w, h)]),
        }
    )
    return writer._add_object(stream)  # pypdf has no public equivalent


def _dot(cx: float, cy: float, rad: float) -> str:
    """PDF operators for a filled circle, as four Bézier quarters."""
    c = KAPPA * rad
    return (
        f"{cx + rad:.3f} {cy:.3f} m "
        f"{cx + rad:.3f} {cy + c:.3f} {cx + c:.3f} {cy + rad:.3f} {cx:.3f} {cy + rad:.3f} c "
        f"{cx - c:.3f} {cy + rad:.3f} {cx - rad:.3f} {cy + c:.3f} {cx - rad:.3f} {cy:.3f} c "
        f"{cx - rad:.3f} {cy - c:.3f} {cx - c:.3f} {cy - rad:.3f} {cx:.3f} {cy - rad:.3f} c "
        f"{cx + c:.3f} {cy - rad:.3f} {cx + rad:.3f} {cy - c:.3f} {cx + rad:.3f} {cy:.3f} c f"
    )


def _check(s: float) -> str:
    """PDF operators for a check mark in a square of side s."""
    return (
        f"{s * 0.15:.3f} w 1 J 1 j {s * 0.16:.3f} {s * 0.52:.3f} m "
        f"{s * 0.41:.3f} {s * 0.24:.3f} l {s * 0.86:.3f} {s * 0.82:.3f} l S"
    )


def _restyle_buttons(writer: PdfWriter, shapes: dict[tuple[str, str], str]) -> int:
    """Give every radio button and checkbox a clean appearance of our own.

    reportlab draws a hairline circle in the "off" state whatever the border width, and a
    ZapfDingbats glyph when on. Off is now empty (the printed box shows through); on is a
    red check in a square box, a red dot in a round one.
    """
    colour = f"{RED.red:.4f} {RED.green:.4f} {RED.blue:.4f}"
    done = 0
    for page in writer.pages:
        for ref in page.get("/Annots", []):
            a = ref.get_object()
            parent = a.get("/Parent")
            parent = parent.get_object() if parent is not None else None
            ft = a.get("/FT") or (parent.get("/FT") if parent else None)
            if a.get("/Subtype") != "/Widget" or ft != "/Btn":
                continue
            name = str(a.get("/T") or parent["/T"])
            on = next(k for k in a["/AP"]["/N"] if k != "/Off")
            x0, y0, x1, y1 = (float(v) for v in a["/Rect"])
            w, h = x1 - x0, y1 - y0
            if shapes[(name, on[1:])] == "round":
                ops, caption = f"{colour} rg {_dot(w / 2, h / 2, min(w, h) * 0.3)}", "l"
            else:
                ops, caption = f"{colour} RG {_check(min(w, h))}", "4"
            a[NameObject("/AP")] = DictionaryObject(
                {
                    NameObject("/N"): DictionaryObject(
                        {
                            NameObject(on): _form_xobject(writer, w, h, ops),
                            NameObject("/Off"): _form_xobject(writer, w, h, ""),
                        }
                    ),
                }
            )
            # What a viewer draws if it redraws the button: ZapfDingbats check or dot.
            a[NameObject("/MK")] = DictionaryObject({NameObject("/CA"): TextStringObject(caption)})
            done += 1
    return done


def main() -> int:
    """Build the PDF; return a process exit code."""
    _truetype_inter()
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH; it renders the page with Playwright", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        printed, fields_json = tmpd / "printed.pdf", tmpd / "fields.json"
        fields_pdf = tmpd / "fields.pdf"
        render = [node, str(HERE / "render.mjs"), str(printed), str(fields_json)]
        subprocess.run(render, check=True)  # noqa: S603 (our own script, our own paths)
        report = json.loads(fields_json.read_text())
        _fields_pdf(report, fields_pdf)

        base = PdfReader(printed)
        if len(base.pages) != report["pages"]:
            print(
                f"Chromium printed {len(base.pages)} pages, the layout has {report['pages']}",
                file=sys.stderr,
            )
            return 1
        writer = PdfWriter(clone_from=fields_pdf)
        for page, printed_page in zip(writer.pages, base.pages, strict=True):
            page.merge_page(printed_page, over=False)
            page.compress_content_streams()  # the merge leaves them uncompressed: ~1 MB
        writer.add_metadata(
            {
                "/Title": "What should it do next? A participatory feature request for ifc-sql-mcp",
                "/Author": "gewoonkees",
                "/Subject": "Twenty features for architects to vote on, mark up and send back",
                "/Keywords": "IFC, Revit, PostgreSQL, data quality, MCP, participatory audit",
            }
        )
        shapes = {
            (f["name"], f["value"] or "Yes"): f["shape"]
            for f in report["fields"]
            if f["kind"] in ("radio", "check")
        }
        buttons = _restyle_buttons(writer, shapes)
        if buttons != len(shapes):
            print(f"restyled {buttons} buttons, expected {len(shapes)}", file=sys.stderr)
            return 1
        with OUT.open("wb") as fh:
            writer.write(fh)
    kb = OUT.stat().st_size // 1024
    print(f"wrote {OUT} ({report['pages']} pages, {len(report['fields'])} form fields, {kb} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
