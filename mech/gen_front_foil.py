#!/usr/bin/env python3
"""Generate the front foil for the ad8317 power meter (Hammond 1590A lid).

Outputs (next to this script):
  front_foil_1590A.dxf               editable CAD file (A4 sheet, 12 foils as block FOIL)
  front_foil_1590A_print_A4.pdf      colour laser print sheet, 1:1, A4 portrait
  front_foil_1590A_test_sheet_A4.pdf plain-paper feed-orientation test sheet

Foil coordinates: origin = lid/board centre, x right, y DOWN, viewed from the top
(same view as the KiCad 3D viewer, F-side up).  Edit the parameters below.
Requires: pip install ezdxf reportlab
"""
import math
from pathlib import Path

import ezdxf
from reportlab.lib.colors import Color, black, white
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- parameters
# Lid top flat face (from 1590A.stp): 88.2 x 34.0 mm; foil is inset ~0.6 mm per side.
FOIL_W, FOIL_H, FOIL_R = 87.0, 33.0, 5.0
BLEED = 1.0                      # colour bleed beyond the cut line

# PCB positions relative to board centre (KiCad: board centre 177.4/84.8)
# Upper circle = "+ NEXT", lower circle = "- MENU" (plus/next on top, minus/menu below).
# Lever posts (see gen_levers.py): the switches SW1/SW2 are under the OLED module, so each
# switch drives a lever whose push post comes through the lid right of the module.
SW1 = (21.5, 5.0)                # upper post: "+ NEXT" (foil y down = -y_up)
SW2 = (21.5, 11.8)               # lower post: "- MENU"
BTN_D = 5.0                      # printed circle diameter (post dia 3.6, not cut)
# SBC-OLED01 (27 x 27 mm).  The Joy-IT datasheet has NO mechanical drawing, so the
# window is derived from the J3 header position (x 3.6, y -12.9) and typical 0.96"
# SSD1306 module geometry.  MEASURE THE REAL MODULE AND ADJUST THESE THREE VALUES.
WIN_W, WIN_H = 22.4, 11.5        # active area 21.74 x 10.86 + 0.3 mm margin
WIN_C = (3.6, 2.8)               # window centre
WIN_R = 0.5                      # corner radius of window

# RF input specification printed next to "RF IN" (AD8317, doc/ADC_Signal_Path_Analysis.md,
# doc/Lab_Optimization_DC_to_6GHz.md).  Max DC = 50 V rating of the DC-block caps C1/C2.
# Max RF = +12 dBm AD8317 absolute maximum; the 50 ohm termination R1 || R2 (2 x 100R 0402,
# 1/16 W = 62.5 mW each, 125 mW total = +21 dBm) is not the limiting part.
RF_SPEC = ["RF IN  50 ohm",
           "Range: -55...0 dBm",
           "Max RF: +12 dBm",
           "Max DC: 50 V"]

NAVY = Color(0.05, 0.16, 0.33)
GREY = Color(0.45, 0.45, 0.45)

# A4 sheet layout
A4_W, A4_H = 210.0, 297.0
COLS, ROWS = 2, 6
GAP_X, GAP_Y = 10.0, 10.0
GRID_W = COLS * FOIL_W + (COLS - 1) * GAP_X
GRID_H = ROWS * FOIL_H + (ROWS - 1) * GAP_Y
X0 = (A4_W - GRID_W) / 2 + FOIL_W / 2            # centre of first foil (sheet, y down)
Y0 = 26.0 + FOIL_H / 2


def foil_centres():
    for r in range(ROWS):
        for c in range(COLS):
            yield X0 + c * (FOIL_W + GAP_X), Y0 + r * (FOIL_H + GAP_Y)


# ---------------------------------------------------------------- scene (foil local, y down)
# ("rrect", cx, cy, w, h, r, kind, fill)  kind: cut | art | hole
# ("circle", cx, cy, d, kind) ; ("text", x, y, h, s, align, color) ; ("line", x1,y1,x2,y2)
def scene():
    s = []
    s.append(("rrect", 0, 0, FOIL_W, FOIL_H, FOIL_R, "cut", None))
    # header band (bleeds past the cut line on top/left/right via clipping at export)
    s.append(("band", -FOIL_W / 2 - BLEED, -FOIL_H / 2 - BLEED, FOIL_W + 2 * BLEED, 8.0 + BLEED))
    s.append(("text", -FOIL_W / 2 + 3.0, -FOIL_H / 2 + 4.6, 4.2, "AD8317 RF POWER METER", "l", white))
    s.append(("text", FOIL_W / 2 - 3.0, -FOIL_H / 2 + 4.6, 3.0, "1 MHz - 10 GHz", "r", white))
    for i, t in enumerate(RF_SPEC):
        s.append(("text", -FOIL_W / 2 + 3.0, -4.8 + i * 3.6, 3.0 if i == 0 else 2.4, t, "l", NAVY))
    s.append(("text", FOIL_W / 2 - 3.0, 0.0, 3.0, "USB-C", "r", NAVY))
    s.append(("text", SW1[0] + BTN_D / 2 + 1.5, SW1[1], 2.6, "+  NEXT", "l", NAVY))
    s.append(("text", SW2[0] + BTN_D / 2 + 1.5, SW2[1], 2.6, "-  MENU", "l", NAVY))
    s.append(("rrect", WIN_C[0], WIN_C[1], WIN_W, WIN_H, WIN_R, "hole", None))
    s.append(("circle", SW1[0], SW1[1], BTN_D, "hole"))
    s.append(("circle", SW2[0], SW2[1], BTN_D, "hole"))
    return s


# ---------------------------------------------------------------- DXF
def rrect_pts(cx, cy, w, h, r):
    """Rounded-rect as LWPOLYLINE vertices (x, y, bulge), CCW, y up."""
    x1, x2, y1, y2 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    b = math.tan(math.pi / 8)
    return [(x1 + r, y1, 0), (x2 - r, y1, b), (x2, y1 + r, 0), (x2, y2 - r, b),
            (x2 - r, y2, 0), (x1 + r, y2, b), (x1, y2 - r, 0), (x1, y1 + r, b)]


def build_dxf(path):
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    for name, col in (("SHEET", 8), ("FOIL_CUT", 1), ("HOLES_CUT", 5), ("ART_BAND", 5),
                      ("ART_TEXT", 7), ("MARKS", 3), ("BUTTON_MARK", 3), ("NOTES", 2)):
        doc.layers.add(name, color=col)
    blk = doc.blocks.new("FOIL")
    for it in scene():
        k = it[0]
        if k == "rrect":
            _, cx, cy, w, h, r, kind, _f = it
            blk.add_lwpolyline(rrect_pts(cx, -cy, w, h, r), format="xyb", close=True,
                               dxfattribs={"layer": "FOIL_CUT" if kind == "cut" else "HOLES_CUT"})
        elif k == "circle":
            blk.add_circle((it[1], -it[2]), it[3] / 2, dxfattribs={"layer": "BUTTON_MARK"})
        elif k == "band":
            _, x, y, w, h = it
            hatch = blk.add_hatch(color=5, dxfattribs={"layer": "ART_BAND"})
            hatch.paths.add_polyline_path(
                [(x, -y), (x + w, -y), (x + w, -(y + h)), (x, -(y + h))], is_closed=True)
        elif k == "text":
            _, x, y, h, txt, al, col = it
            t = blk.add_text(txt, height=h * 0.72,
                             dxfattribs={"layer": "ART_TEXT", "style": "OpenSans"
                                         if "OpenSans" in doc.styles else "Standard",
                                         "color": 7 if col == white else 5})
            t.set_placement((x, -y), align={"l": ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT,
                                            "r": ezdxf.enums.TextEntityAlignment.MIDDLE_RIGHT}[al])
    # centre marks (outside the cut: registration/reference)
    for dx in (-FOIL_W / 2 - 3, FOIL_W / 2 + 3):
        blk.add_line((dx - 1.5, 0), (dx + 1.5, 0), dxfattribs={"layer": "MARKS"})
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (A4_W, 0), (A4_W, A4_H), (0, A4_H)], close=True,
                       dxfattribs={"layer": "SHEET"})
    for cx, cy in foil_centres():
        msp.add_blockref("FOIL", (cx, A4_H - cy))
    msp.add_text("TOP (leading edge in manual feed tray - verify with test sheet)", height=3,
                 dxfattribs={"layer": "NOTES"}).set_placement((A4_W / 2, A4_H - 8),
                                                              align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    msp.add_text("Front foil Hammond 1590A, 1:1, mm. Cut: knife on FOIL_CUT, knife on HOLES_CUT (window only; BUTTON_MARK circles are NOT cut)",
                 height=2.5, dxfattribs={"layer": "NOTES"}).set_placement(
        (A4_W / 2, 10), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(path)


# ---------------------------------------------------------------- PDF
def mm(v):
    return v * 72.0 / 25.4


def pdf_foil(c, cx, cy, marks=True):
    """cx, cy: foil centre on sheet in mm, y down."""
    H = A4_H
    P = lambda x, y: (mm(cx + x), mm(H - (cy + y)))
    c.saveState()
    # clip to cut outline + bleed so colour bleeds slightly past the knife line
    p = c.beginPath()
    bx, by = P(-FOIL_W / 2 - BLEED, FOIL_H / 2 + BLEED)
    p.roundRect(bx, by, mm(FOIL_W + 2 * BLEED), mm(FOIL_H + 2 * BLEED), mm(FOIL_R + BLEED))
    c.clipPath(p, stroke=0, fill=0)
    for it in scene():
        k = it[0]
        if k == "band":
            _, x, y, w, h = it
            c.setFillColor(NAVY)
            px, py = P(x, y + h)
            c.rect(px, py, mm(w), mm(h), stroke=0, fill=1)
        elif k == "text":
            _, x, y, h, txt, al, col = it
            c.setFillColor(col)
            c.setFont("Helvetica-Bold", h * 72 / 25.4 * 0.72)
            px, py = P(x, y + h * 0.26)
            {"l": c.drawString, "r": c.drawRightString}[al](px, py, txt)
    c.restoreState()
    # hole outlines (hairline, grey) and knife-cut guide ticks outside the cut line
    c.setStrokeColor(GREY)
    c.setLineWidth(0.15)
    for it in scene():
        if it[0] == "rrect" and it[6] == "hole":
            _, x, y, w, h, r, _k, _f = it
            px, py = P(x - w / 2, y + h / 2)
            c.roundRect(px, py, mm(w), mm(h), mm(r), stroke=1, fill=0)
        elif it[0] == "circle":
            px, py = P(it[1], it[2])
            c.circle(px, py, mm(it[3] / 2), stroke=1, fill=0)
    if marks:
        c.setStrokeColor(black)
        c.setLineWidth(0.25)
        t = 3.0
        for sx in (-1, 1):
            for sy in (-1, 1):
                x = sx * (FOIL_W / 2 + 1.5)
                y = sy * (FOIL_H / 2 + 1.5)
                c.line(*P(x, y), *P(x + sx * t, y))
                c.line(*P(x, y), *P(x, y + sy * t))


def build_print(path):
    c = canvas.Canvas(str(path), pagesize=(mm(A4_W), mm(A4_H)))
    c.setTitle("Front foil 1590A - print sheet")
    for cx, cy in foil_centres():
        pdf_foil(c, cx, cy)
    c.setFillColor(black)
    c.setFont("Helvetica", 7)
    c.drawCentredString(mm(A4_W / 2), mm(A4_H - 12),
                        "Print at 100% / actual size (no 'fit to page'), colour, manual tray. "
                        "Check: bar below = 100 mm.")
    c.setLineWidth(0.5)
    c.line(mm(55), mm(A4_H - 17), mm(155), mm(A4_H - 17))
    for x in (55, 155):
        c.line(mm(x), mm(A4_H - 15.5), mm(x), mm(A4_H - 18.5))
    c.setFont("Helvetica-Bold", 7)
    c.drawString(mm(A4_W / 2 - 18), mm(A4_H - 15), "TOP OF SHEET")
    c.save()


def build_test(path):
    c = canvas.Canvas(str(path), pagesize=(mm(A4_W), mm(A4_H)))
    c.setTitle("Front foil 1590A - feed test sheet")
    c.setFont("Helvetica-Bold", 60)
    c.drawCentredString(mm(A4_W / 2), mm(A4_H - 90), "TOP")
    # big arrow towards the top edge
    cx = mm(A4_W / 2)
    c.setFillColor(black)
    p = c.beginPath()
    p.moveTo(cx, mm(A4_H - 12))
    p.lineTo(cx - mm(20), mm(A4_H - 40))
    p.lineTo(cx - mm(8), mm(A4_H - 40))
    p.lineTo(cx - mm(8), mm(A4_H - 62))
    p.lineTo(cx + mm(8), mm(A4_H - 62))
    p.lineTo(cx + mm(8), mm(A4_H - 40))
    p.lineTo(cx + mm(20), mm(A4_H - 40))
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(cx, mm(A4_H - 125), "THIS SIDE = PRINTED SIDE (face F)")
    c.setFont("Helvetica", 11)
    lines = ["1. Take a plain A4 sheet. With a pencil draw a small arrow on ONE face, at the edge",
             "    you will insert FIRST into the manual tray (this face = 'F', arrow = leading edge).",
             "2. Load it in the manual tray exactly as you plan to load the foil",
             "    (e.g. face F up/down as the tray requires, arrow edge towards the printer).",
             "3. Print this PDF at 100 % scale, colour.",
             "4. Result: if the printed TOP arrow is at the pencil-arrow edge AND the print is on",
             "    face F, the loading is correct. Otherwise rotate 180 deg and/or flip, repeat.",
             "5. Load the foil the same way. The printed TOP edge = top of the sheet of foils",
             "    (foil layout on the print sheet is not symmetric: header text at the top)."]
    for i, t in enumerate(lines):
        c.drawString(mm(20), mm(A4_H - 145 - i * 6.5), t)
    # 100 mm scale bar to verify 100% print scale
    c.setStrokeColor(black)
    c.setLineWidth(0.5)
    c.line(mm(55), mm(40), mm(155), mm(40))
    for x in (55, 155):
        c.line(mm(x), mm(38.5), mm(x), mm(41.5))
    c.setFont("Helvetica", 9)
    c.drawCentredString(mm(A4_W / 2), mm(32), "bar = 100 mm (measure to check print scale)")
    c.save()


if __name__ == "__main__":
    build_dxf(OUT / "front_foil_1590A.dxf")
    build_print(OUT / "front_foil_1590A_print_A4.pdf")
    build_test(OUT / "front_foil_1590A_test_sheet_A4.pdf")
    print("written to", OUT)
