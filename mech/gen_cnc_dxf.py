#!/usr/bin/env python3
"""CNC-table DXF files (mm) for the Hammond 1590A: lid top and the two end walls.

Machine frame: X right, Y away from the operator, Z up (spindle).  Each file is one set-up:
the face to be machined is UP, the work zero (X0 Y0) is the lower-left corner of the part
bounding box as seen by the spindle, Z0 = the machined (outer) surface, cuts go to negative Z.
Numbers match gen_levers.py (board frame: origin board centre, x right, y up, z = 0 on PCB top).
"""
from pathlib import Path
import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

OUT = Path(__file__).resolve().parent
LID_HX, LID_HY = 46.15, 19.05          # lid bounding box half sizes (flange)
BASE_HW = 19.25                        # base outer half width (y)
BASE_H = 27.0                          # base height (z -18.06 .. +8.94)
Z_BASE_BOT = -18.06
WALL_T = 2.2                           # end wall thickness (outer 46.3, inner 44.1)

LID = dict(
    window=dict(c=(3.6, -2.8), w=23.4, h=12.5, r=1.0),
    holes=[dict(c=(21.5, -5.0), d=4.2, nm="NEXT (+) post"), dict(c=(21.5, -11.8), d=4.2, nm="MENU (-) post")],
)
SMA = dict(y=-0.16, z=0.5, d=6.8)
USB = dict(y=-0.1, z=1.19, w=12.6, h=6.6)


def new_doc(title):
    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    for n, c in (("STOCK", 8), ("CUT_THROUGH", 1), ("CENTER", 3), ("AXES", 2), ("TEXT", 7)):
        doc.layers.add(n, color=c)
    return doc, doc.modelspace()


def text(m, x, y, s, h=1.8, al=TA.MIDDLE_LEFT):
    m.add_text(s, height=h, dxfattribs={"layer": "TEXT"}).set_placement((x, y), align=al)


def notes(m, W, H, lines):
    """Annotation block to the right of the part (never over the geometry)."""
    for i, t in enumerate(lines):
        text(m, W + 6, H - 3 - i * 3.2, t, 1.6)


def cross(m, x, y, s=2.5):
    m.add_line((x - s, y), (x + s, y), dxfattribs={"layer": "CENTER"})
    m.add_line((x, y - s), (x, y + s), dxfattribs={"layer": "CENTER"})


def rect(m, x0, y0, x1, y1, layer):
    m.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def slot(m, cx, cy, w, h, r, layer="CUT_THROUGH"):
    """Rounded rectangle (r = h/2 gives a stadium)."""
    x1, x2, y1, y2 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    b = 0.41421356
    m.add_lwpolyline([(x1 + r, y1, 0), (x2 - r, y1, b), (x2, y1 + r, 0), (x2, y2 - r, b),
                      (x2 - r, y2, 0), (x1 + r, y2, b), (x1, y2 - r, 0), (x1, y1 + r, b)],
                     format="xyb", close=True, dxfattribs={"layer": layer})


def axes(m, what):
    m.add_line((0, 0), (12, 0), dxfattribs={"layer": "AXES"})
    m.add_line((0, 0), (0, 12), dxfattribs={"layer": "AXES"})
    text(m, 13, 0, "X+", 2.0)
    text(m, 0, 14, "Y+", 2.0, TA.BOTTOM_CENTER)
    text(m, -2, -4, "X0 Y0 (work zero, lower-left corner of the bounding box)  Z0 = " + what, 1.6)


def lid():
    doc, m = new_doc("lid")
    W, H = 2 * LID_HX, 2 * LID_HY
    rect(m, 0, 0, W, H, "STOCK")
    ox, oy = LID_HX, LID_HY                     # board centre in CNC coordinates
    wd = LID["window"]
    cx, cy = wd["c"][0] + ox, wd["c"][1] + oy
    slot(m, cx, cy, wd["w"], wd["h"], wd["r"])
    cross(m, cx, cy)
    lines = ["LID - TOP FACE UP (view from the spindle), set-up 1",
             "Bounding box %.2f x %.2f (flange). SMA end = left (X low), USB-C end = right." % (W, H),
             "Z0 = outer top surface of the lid; lid plate is 2.0 mm thick -> cut to Z-2.2 (sacrificial backing).",
             "",
             "1  OLED WINDOW %.1f x %.1f, corner R%.1f -> cutter dia <= 2.0 mm" % (wd["w"], wd["h"], wd["r"]),
             "   centre X%.2f Y%.2f, contour inside the line / pocket" % (cx, cy)]
    for n, h_ in enumerate(LID["holes"], 2):
        hx, hy = h_["c"][0] + ox, h_["c"][1] + oy
        m.add_circle((hx, hy), h_["d"] / 2, dxfattribs={"layer": "CUT_THROUGH"})
        cross(m, hx, hy)
        text(m, hx, hy + 4.2, str(n), 2.0, TA.MIDDLE_CENTER)
        lines += ["%d  %s dia %.1f through, centre X%.2f Y%.2f" % (n, h_["nm"], h_["d"], hx, hy)]
    text(m, cx, cy + 9, "1", 2.0, TA.MIDDLE_CENTER)
    notes(m, W, H, lines)
    axes(m, "outer top face of the lid")
    doc.saveas(OUT / "cnc_lid_top.dxf")


def wall(name, side):
    doc, m = new_doc(name)
    W, H = 2 * BASE_HW, BASE_H
    rect(m, 0, 0, W, H, "STOCK")
    # outside view: left wall -> viewer's left = +y ; right wall -> viewer's left = -y
    if side == "left":
        x = BASE_HW - SMA["y"]
        y = SMA["z"] - Z_BASE_BOT
        m.add_circle((x, y), SMA["d"] / 2, dxfattribs={"layer": "CUT_THROUGH"})
        lines = ["LEFT END WALL (SMA) - OUTSIDE FACE UP, set-up 2",
                 "1  SMA hole dia %.1f through the wall (%.1f mm), cut to Z-2.5, centre X%.2f Y%.2f" % (SMA["d"], WALL_T, x, y),
                 "   connector barrel passes through and protrudes ~3.7 mm outside the wall"]
        fname = "cnc_wall_left_SMA.dxf"
    else:
        x = BASE_HW + USB["y"]
        y = USB["z"] - Z_BASE_BOT
        slot(m, x, y, USB["w"], USB["h"], USB["h"] / 2)
        lines = ["RIGHT END WALL (USB-C) - OUTSIDE FACE UP, set-up 3",
                 "1  USB-C opening %.1f x %.1f full radius through the wall (%.1f mm), cut to Z-2.5," % (USB["w"], USB["h"], WALL_T),
                 "   centre X%.2f Y%.2f. Receptacle face is 2.7 mm behind the outer wall surface" % (x, y)]
        fname = "cnc_wall_right_USB.dxf"
    cross(m, x, y)
    text(m, x, y + 5.5, "1", 2.0, TA.MIDDLE_CENTER)
    lines += ["", "Bounding box %.1f (case width) x %.1f (case height). Y0 = case bottom, joint plane at Y%.2f." % (W, H, H),
              "Z0 = outer surface of the end wall."]
    notes(m, W, H, lines)
    axes(m, "outer surface of the end wall")
    doc.saveas(OUT / fname)


if __name__ == "__main__":
    lid()
    wall("left", "left")
    wall("right", "right")
    print("written to", OUT)
