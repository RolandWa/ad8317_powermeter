# Run: set AD_ROOT=<repo root> [AD_BOARD=<kicad-cli exported board .step>] ; freecadcmd gen_levers.py
"""Button levers, lever bracket and machined Hammond 1590A for the ad8317 power meter.

Frame (all dimensions mm): origin = board centre, x right, y UP (= -KiCad y), z = 0 on the
PCB top surface, +z towards the lid.  Same frame as lib/3dmodels/Hammond_Cases.3dshapes/
Hammond_1590A_kicad.step.

Why levers: SW1/SW2 sit exactly under the SBC-OLED01 module (plugged into J3), so straight
pushers cannot reach the lid.  Two stacked levers pivot at x = -13 (outside the module),
pass under the module and carry a push post through the lid to the right of the module.
"""
import math
import sys
from pathlib import Path

import FreeCAD
import Mesh
import MeshPart
import Part
from FreeCAD import Vector as V

import os
ROOT = Path(os.environ["AD_ROOT"])
BOARD = Path(os.environ["AD_BOARD"]) if os.environ.get("AD_BOARD") else None
OUT = ROOT / "mech" / "levers"
OUT.mkdir(exist_ok=True)

# ----------------------------------------------------------------------------- parameters
Z_LID_IN, Z_LID_OUT = 10.94, 12.94      # lid plate (from 1590A.stp)
SW_TOP = 3.1                            # B3FS-100xP actuator top
SW1 = (15.1, -11.8)                     # MENU  "-"  (lower post on the foil)
SW2 = (3.1, -11.8)                      # NEXT  "+"  (upper post on the foil)
POST1 = (21.5, -5.0)                    # NEXT "+" post (through lid) - upper on the foil
POST2 = (21.5, -11.8)                   # MENU "-" post - lower on the foil
BEND1 = (17.5, -11.8)                   # NEXT lever (lower layer): straight to here, then angled to POST1
PIVOT_X = -13.0
ARM_W = 7.0                             # lever width (lid cavity inner |y| = 15.7 above z = 8.94)
NECK_W_ARM = 4.6                        # narrow pivot end (so the bracket ears stay inside |y| < 15.5)
NECK_X = -9.0                           # neck ends / full width starts
L1_Z0, L1_T = 3.5, 2.3                  # lower lever = NEXT, arm z 3.5 .. 5.8
L2_Z0, L2_T = 6.2, 1.8                  # upper lever = MENU, arm z 6.2 .. 8.0 (module underside ~8.3)
STEM_D = 3.6
LID_HOLE_D = 4.2
SHOULDER_D, SHOULDER_T = 6.4, 1.0
DOME_TOP = 13.15                        # post top at rest (foil bulge 0.2)
DOME_R = 8.0
FOOT_D = 2.6
PEG_D, PEG_L, PEG_CH = 1.6, 0.9, 0.3    # printed axle pegs on the lever sides
HOLE_D = 1.8                            # hole in the bracket ear
NECK_W = 1.5                            # snap neck of the open-bottom slot
EAR_T, EAR_GAP = 0.8, 0.3
WIN_C, WIN_W, WIN_H = (3.6, -2.8), 23.4, 12.5   # lid window (foil window 22.4 x 11.5)
SMA = dict(y=-0.16, z=0.5, d=6.8)       # barrel dia 6.35 + 0.45
USB = dict(y=-0.1, z=1.19, w=12.6, h=6.6)       # plug overmould stadium


def box(x0, x1, y0, y1, z0, z1):
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, V(x0, y0, z0))


def cyl(x, y, z0, z1, d):
    return Part.makeCylinder(d / 2, z1 - z0, V(x, y, z0), V(0, 0, 1))


def post(x, y, z_arm_top):
    p = cyl(x, y, z_arm_top - 0.2, Z_LID_IN - SHOULDER_T, STEM_D)
    p = p.fuse(cyl(x, y, Z_LID_IN - SHOULDER_T, Z_LID_IN, SHOULDER_D))
    p = p.fuse(cyl(x, y, Z_LID_IN, Z_LID_OUT, STEM_D))
    cap_h = DOME_R - math.sqrt(DOME_R ** 2 - (STEM_D / 2) ** 2)
    z_base = DOME_TOP - cap_h
    p = p.fuse(cyl(x, y, Z_LID_OUT, z_base, STEM_D))
    sph = Part.makeSphere(DOME_R, V(x, y, DOME_TOP - DOME_R))
    p = p.fuse(sph.common(cyl(x, y, z_base - 0.01, DOME_TOP + 1, STEM_D)))
    return p


def arm_solid(path, w, z0, z1):
    """One continuous plate: rectangles along the centre line plus a full disc at every vertex
    (round ends and round inner/outer corners, no gaps at the bends)."""
    parts_ = []
    for p0, p1 in zip(path, path[1:]):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(dx, dy)
        nx, ny = -dy / n * w / 2, dx / n * w / 2
        pts = [V(p0[0] + nx, p0[1] + ny, z0), V(p1[0] + nx, p1[1] + ny, z0),
               V(p1[0] - nx, p1[1] - ny, z0), V(p0[0] - nx, p0[1] - ny, z0)]
        pts.append(pts[0])
        parts_.append(Part.Face(Part.makePolygon(pts)).extrude(V(0, 0, z1 - z0)))
    for p in path:
        parts_.append(cyl(p[0], p[1], z0, z1, w))
    s = parts_[0]
    for q in parts_[1:]:
        s = s.fuse(q)
    return s.removeSplitter()


def pegs(y, z):
    out = None
    half = NECK_W_ARM / 2
    for sgn in (-1, 1):
        base = V(PIVOT_X, y + sgn * (half - 0.2), z)           # start inside the arm
        peg = Part.makeCylinder(PEG_D / 2, PEG_L + 0.2 - PEG_CH, base, V(0, sgn, 0))
        tip = Part.makeCone(PEG_D / 2, PEG_D / 2 - PEG_CH, PEG_CH,
                            V(PIVOT_X, y + sgn * (half + PEG_L - PEG_CH), z), V(0, sgn, 0))
        peg = peg.fuse(tip)
        out = peg if out is None else out.fuse(peg)
    return out


def lever_menu():
    """Upper layer: foot on SW1 (x 15.1), straight arm to the lower post."""
    z0, z1 = L2_Z0, L2_Z0 + L2_T
    y = SW1[1]
    s = arm_solid([(NECK_X, y), POST2], ARM_W, z0, z1)
    s = s.fuse(arm_solid([(PIVOT_X, y), (NECK_X, y)], NECK_W_ARM, z0, z1))
    s = s.fuse(cyl(SW1[0], SW1[1], SW_TOP, z0 + 0.1, FOOT_D))
    s = s.fuse(post(POST2[0], POST2[1], z1))
    s = s.fuse(pegs(y, (z0 + z1) / 2))
    return s.removeSplitter()


def lever_next():
    """Lower layer: foot on SW2 (x 3.1), straight arm then angled arm to the upper post."""
    z0, z1 = L1_Z0, L1_Z0 + L1_T
    y = SW2[1]
    s = arm_solid([(NECK_X, y), BEND1, POST1], ARM_W, z0, z1)
    s = s.fuse(arm_solid([(PIVOT_X, y), (NECK_X, y)], NECK_W_ARM, z0, z1))
    s = s.fuse(cyl(SW2[0], SW2[1], SW_TOP, z0 + 0.1, FOOT_D))
    s = s.fuse(post(POST1[0], POST1[1], z1))
    s = s.fuse(pegs(y, (z0 + z1) / 2))
    s = s.cut(box(SW1[0] - 2.4, SW1[0] + 2.4, y - 1.7, y + 1.7, z0 - 1, z1 + 1))   # MENU foot slot
    return s.removeSplitter()


def bracket():
    """Inverted U: pad bonded under the lid + two flexible ears with axle holes."""
    y = SW2[1]
    yo = NECK_W_ARM / 2 + EAR_GAP + EAR_T / 2
    s = box(-17.0, -10.6, y - yo - EAR_T / 2, y + yo + EAR_T / 2, Z_LID_IN - 1.5, Z_LID_IN)
    for sgn in (-1, 1):
        ear = box(-15.0, -11.0, y + sgn * yo - EAR_T / 2, y + sgn * yo + EAR_T / 2, 3.3, Z_LID_IN - 1.5)
        s = s.fuse(ear)
    s = s.removeSplitter()
    z_hi = L2_Z0 + L2_T / 2
    # open-bottom slot (neck 1.5 < peg 1.6: light snap) so the levers are pushed in from below;
    # round axle holes at the two peg heights
    s = s.cut(box(PIVOT_X - NECK_W / 2, PIVOT_X + NECK_W / 2, y - 15, y + 15, 3.0, z_hi))
    for z in (L1_Z0 + L1_T / 2, z_hi):
        s = s.cut(Part.makeCylinder(HOLE_D / 2, 30, V(PIVOT_X, y - 15, z), V(0, 1, 0)))
    return s.removeSplitter()


def machined_case():
    case = Part.read(str(ROOT / "lib/3dmodels/Hammond_Cases.3dshapes/Hammond_1590A_kicad.step"))
    cuts = []
    for p in (POST1, POST2):
        cuts.append(cyl(p[0], p[1], Z_LID_IN - 0.5, Z_LID_OUT + 1, LID_HOLE_D))
    w = box(-WIN_W / 2, WIN_W / 2, -WIN_H / 2, WIN_H / 2, Z_LID_IN - 0.5, Z_LID_OUT + 1)
    w = w.makeFillet(1.0, [e for e in w.Edges if abs(e.Vertexes[0].Point.z - e.Vertexes[1].Point.z) > 1e-6])
    w.translate(V(WIN_C[0], WIN_C[1], 0))
    cuts.append(w)
    cuts.append(Part.makeCylinder(SMA["d"] / 2, 8, V(-48, SMA["y"], SMA["z"]), V(1, 0, 0)))
    r = USB["h"] / 2
    st = box(43.0, 48.0, USB["y"] - USB["w"] / 2 + r, USB["y"] + USB["w"] / 2 - r, USB["z"] - r, USB["z"] + r)
    for sgn in (-1, 1):
        st = st.fuse(Part.makeCylinder(r, 5, V(43.0, USB["y"] + sgn * (USB["w"] / 2 - r), USB["z"]), V(1, 0, 0)))
    cuts.append(st)
    for c in cuts:
        case = case.cut(c)
    return case


def export(shape, name):
    shape.exportStep(str(OUT / f"{name}.step"))
    m = MeshPart.meshFromShape(shape, LinearDeflection=0.02, AngularDeflection=0.2)
    m.write(str(OUT / f"{name}.stl"))


parts = {"lever_menu_SW1": lever_menu(), "lever_next_SW2": lever_next(), "lever_bracket": bracket()}
for n, s in parts.items():
    export(s, n)
    print("PART", n, round(s.Volume, 1), s.isValid())
case = machined_case()
case.exportStep(str(ROOT / "mech" / "hammond_1590A_machined.step"))
print("CASE", round(case.Volume), case.isValid())

# ----------------------------------------------------------------------------- checks
module = box(3.6 - 13.65, 3.6 + 13.65, 12.9 + 1.3 - 27.3, 12.9 + 1.3, 8.3, 10.94)   # SBC-OLED01 envelope (assumed)
doc = FreeCAD.newDocument("asm")
for n, s in parts.items():
    doc.addObject("Part::Feature", n).Shape = s
doc.addObject("Part::Feature", "case_machined").Shape = case
doc.addObject("Part::Feature", "oled_module_envelope").Shape = module
import Import
Import.export(doc.Objects, str(ROOT / "mech" / "levers" / "assembly_check.step"))

def common_vol(a, b):
    try:
        return a.common(b).Volume
    except Exception as e:  # noqa
        return float("nan")

print("CHECK menu vs next", round(common_vol(parts["lever_menu_SW1"], parts["lever_next_SW2"]), 3))
for n, s in parts.items():
    print("CHECK", n, "vs module", round(common_vol(s, module), 3), "vs case", round(common_vol(s, case), 3))
print("CHECK bracket vs levers", round(common_vol(parts["lever_bracket"], parts["lever_menu_SW1"]), 3),
      round(common_vol(parts["lever_bracket"], parts["lever_next_SW2"]), 3))
if BOARD and BOARD.exists():
    b = Part.read(str(BOARD))
    # board STEP: x_abs = 177.4 + x, y_abs = -84.8 + y, z_abs = 1.6 + z
    tr = FreeCAD.Matrix(1, 0, 0, 177.4, 0, 1, 0, -84.8, 0, 0, 1, 1.6, 0, 0, 0, 1)
    for n, s in parts.items():
        t = s.transformGeometry(tr)
        bad = []
        for i, sol in enumerate(b.Solids):
            bb = sol.BoundBox
            if bb.XLength > 80:
                continue
            tb = t.BoundBox
            if (bb.XMax < tb.XMin or bb.XMin > tb.XMax or bb.YMax < tb.YMin or bb.YMin > tb.YMax
                    or bb.ZMax < tb.ZMin or bb.ZMin > tb.ZMax):
                continue
            v = common_vol(t, sol)
            if v > 1e-3:
                ob = sol.optimalBoundingBox(False, False)
                bad.append((round(v, 3), round(ob.XMin - 177.4, 1), round(ob.YMin + 84.8, 1), round(ob.ZMin - 1.6, 2), round(ob.ZMax - 1.6, 2)))
        print("BOARD-CHECK", n, "interferences:", bad)


# ----------------------------------------------------------------------------- continuity
for n in ("lever_menu_SW1", "lever_next_SW2"):
    sh = parts[n]
    bb = sh.optimalBoundingBox(False, False)
    gaps = []
    xs = [bb.XMin + 0.3 + i * 0.5 for i in range(int((bb.XMax - bb.XMin - 0.6) / 0.5))]
    for x in xs:
        if not sh.slice(V(1, 0, 0), x):
            gaps.append(x)
    print("CONT", n, "solids", len(sh.Solids), "valid", sh.isValid(), "slice gaps", gaps)

# ----------------------------------------------------------------------------- stiffness
F_SW = 2.55            # N, worst B3FS variant (assumed; -100xP exact variant not specified)
for E, mat in ((1500.0, "PETG"), (2500.0, "PETG-CF / PA12"), (6000.0, "PA-CF")):
    for name, w, t, z0, xf, xp in (("MENU", ARM_W, L2_T, L2_Z0, SW1[0], POST2[0]),
                                         ("NEXT", ARM_W, L1_T, L1_Z0, SW2[0], SW2[0] + (BEND1[0] - SW2[0]) + math.hypot(POST1[0] - BEND1[0], POST1[1] - BEND1[1]))):
        a = xf - PIVOT_X
        b = xp - PIVOT_X
        F = F_SW * a / b
        Mf = F * (xp - xf)
        n = 400

        def I_at(x):
            rects = [(w, z0, z0 + t)]                       # (width_y, z_bottom, z_top)
            A = sum(wd * (zt - zb) for wd, zb, zt in rects)
            zc = sum(wd * (zt - zb) * (zt + zb) / 2 for wd, zb, zt in rects) / A
            return sum(wd * (zt - zb) ** 3 / 12 + wd * (zt - zb) * ((zt + zb) / 2 - zc) ** 2 for wd, zb, zt in rects)

        dx = (xp - xf) / n
        d_c = 0.0
        for i in range(n):
            x = xf + (i + 0.5) * dx
            d_c += F * (xp - x) * (xp - x) / (E * I_at(x)) * dx
        theta = Mf * a / (3 * E * I_at(0.0))
        d_tot = d_c + theta * (xp - xf)
        sigma = Mf * (t / 2.0) / I_at(xf)
        stroke = 0.25 * b / a
        print("STIFF %s %-14s finger F=%.2f N  tip flex=%.2f mm of %.2f mm post stroke  sigma=%.1f MPa" %
              (name, mat, F, d_tot, stroke + d_tot, sigma))
