# -*- coding: utf-8 -*-
# Run from: AEDT  Tools > Run Script
# IronPython 2.7 / AEDT 2026.1  -  NO external packages required
"""
hfss_sma_coupon_gcpw_wurth_s11.py  -  Würth 60312202114509 GCPW coupon, one-port S11

The Würth 60312202114509 SMA connector is available in HFSS 3D Layout format only
(no .a3dcomp for HFSS 3D Modeler).  This script builds the connector body from
IronPython geometry primitives using Würth datasheet dimensions (rev.001 2018-03-19).
No 3D component file is required.

Connector (Würth 60312202114509 — SMA PCB End Launch Jack, WR-SMA, 1.6mm PCB):
  PCB contact pin Ø: 0.76 mm  (radius 0.38 mm)
  Pin insertion depth: ≈ 1.27 mm  (A-A cross-section dimension)
  SMA cable interface: standard 1/4-36 UNS-2A (inner Ø 1.27 mm, outer inner Ø 4.1 mm)

Parametric connector model (square-cross-section approximation):
  Outer conductor : hollow copper square tube, cross-section ±(OUTER_R+SHELL_T)
  PTFE fill       : square box between outer and inner conductor
  Inner conductor : copper strip matching trace cross-section (TRACE_WIDTH × _OZ1)
  The inner strip extends from the cable face to the PCB edge — no .a3dcomp needed.

One-port setup (S11 tuning):
  WP1 at cable face (x = -(hx + SMA_BODY_LEN)) → GCPW 20 mm → 50 Ω load (x = +hx)

The 50 Ω load (AssignImpedance) terminates the guided GCPW mode so S11 at WP1
reflects only the SMA-to-GCPW transition quality.  Target: S11 < -20 dB over
0.1 – 20 GHz.

Tuning workflow:
  1. Run script — inspect S11 in Results > S Parameters.
  2. Adjust TRACE_WIDTH and GAP_MM in CONFIG.
  3. Re-run (script deletes and rebuilds the design automatically).
  4. Optional: add compensation structures (see hfss_sma_coupon_gcpw_opt.py).

Port impedance note:
  The connector body uses a SQUARE outer conductor (not circular).  The wave port
  (circular disc, r=SMA_OUTER_R) computes the actual TEM-like impedance via
  CharImp="Zpi".  S11 is referenced to this computed impedance.  For the final
  signoff, verify port impedance is close to 50 Ω in the port solutions report.
"""

import os
import sys
import time
import traceback

# ===========================================================================
# CONFIG — change TRACE_WIDTH and GAP_MM to tune S11
# ===========================================================================

N_LAYERS    = 4
SOLDER_MASK = True

BOARD_LENGTH = 20.0   # mm  X  (standard GCPW coupon)
BOARD_WIDTH  = 10.0   # mm  Y

# GCPW target: 50 Ω — 4-layer JLCPCB  h=0.2101mm er=4.5
#   AppCAD/TX-Line initial values:  W≈0.35mm  G≈0.20mm
TRACE_WIDTH      = 0.35   # mm  signal conductor width  ← PRIMARY TUNING VARIABLE
GAP_MM           = 0.20   # mm  coplanar gap each side  ← SECONDARY TUNING VARIABLE

# Via fence
VIA_DRILL_MM     = 0.30   # mm
VIA_PITCH_MM     = 0.80   # mm
VIA_CLEARANCE_MM = 0.05   # mm

# === SMA CONNECTOR — Würth 60312202114509 (from datasheet) ==================
# All connector dimensions used to build the parametric body.
# Body extends from x = -(hx + SMA_BODY_LEN) to x = -hx.
SMA_BODY_LEN = 12.0    # mm  connector body length along board axis (estimated)
SMA_OUTER_R  = 2.05    # mm  SMA outer conductor inner radius (standard SMA 50Ω)
SMA_SHELL_T  = 0.30    # mm  outer conductor wall thickness (for model only)
SMA_INNER_R  = 0.635   # mm  SMA cable inner conductor radius (standard SMA 50Ω)
# PCB contact pin (used only for documentation — inner conductor is a flat strip)
PCB_PIN_D    = 0.76    # mm  PCB contact pin diameter (datasheet Ø 0.76)
PCB_PIN_R    = 0.38    # mm  = PCB_PIN_D / 2
PCB_PIN_LEN  = 1.27    # mm  pin insertion depth into PCB (A-A section)

# Wave port disc radius at cable face — circular disc inside square outer conductor
PORT_DISC_R = SMA_OUTER_R   # mm  matches SMA outer conductor inner radius

# Frequency
F_START     = 0.01    # GHz
F_STOP      = 20.0    # GHz
F_ADAPT     = 16.0    # GHz
F_ADAPT2    = 1.0     # GHz  add via Edit Setup > Multi-Frequency
F_POINTS    = 401
MAX_DELTA_S = 0.05    # loosen for fast tuning iterations; tighten to 0.02 for final
MAX_PASSES  = 6       # raise to 10 for final verification

# Mesh
MESH_TRANS_HX = 3.0
MESH_TRANS_HY = 3.0
MESH_TRANS_HZ = 2.0
MESH_TRANS_MM = 0.15
MESH_TRACE_MM = 0.12
MESH_CGND_MM  = 0.25
MESH_PLANE_MM = 0.60
MESH_VIA_MM   = 0.10
MESH_SKIN_MM  = 0.10

# Materials
ER_FR4   = 4.5
TAND_FR4 = 0.02
ER_SM    = 3.5
TAND_SM  = 0.025
ER_PTFE  = 2.1
TAND_PTFE = 0.0002

PROJECT_NAME = "SMA_Coupon_GCPW_Wurth_S11"
DESIGN_BASE  = "SMA_Coupon_GCPW_Wurth_S11"

# ===========================================================================
# STACKUP DATABASE
# ===========================================================================
_OZ1  = 0.035
_OZ05 = 0.0175
_SM_T = 0.025

_STACKUPS = {
    2: [
        ("B.Cu",   _OZ1,   "copper"),
        ("Sub",    1.5300, "dielectric"),
        ("F.Cu",   _OZ1,   "copper"),
    ],
    4: [
        ("B.Cu",   _OZ1,   "copper"),
        ("Pre2",   0.2101, "dielectric"),
        ("In2.Cu", _OZ05,  "copper"),
        ("Core1",  1.0650, "dielectric"),
        ("In1.Cu", _OZ05,  "copper"),
        ("Pre1",   0.2101, "dielectric"),
        ("F.Cu",   _OZ1,   "copper"),
    ],
}

# ===========================================================================
# Logging
# ===========================================================================
_t0 = time.time()


def _log(msg, severity=0):
    elapsed = time.time() - _t0
    line = "[{:7.2f}s] {}".format(elapsed, msg)
    print(line)
    sys.stdout.flush()
    try:
        oDesktop.AddMessage("", "", severity, line)
    except Exception:
        pass


def _step(title):
    _log("=" * 55)
    _log("STEP: " + title)
    _log("=" * 55)


def _warn(msg):  _log(msg, 1)
def _err(msg):   _log(msg, 2)


def mm(v):
    return "{:.6f}mm".format(float(v))


# ===========================================================================
# Geometry primitives
# ===========================================================================
_CONDUCTORS = {"copper", "pec", "aluminum", "gold", "silver"}


def create_box(oEditor, name, xo, yo, zo, xs, ys, zs, mat,
               transparent=False, nonmodel=False):
    tr    = 0.6 if transparent else 0
    flags = "NonModel#" if nonmodel else ""
    solve_inside = mat.lower() not in _CONDUCTORS
    oEditor.CreateBox(
        ["NAME:BoxParameters",
         "XPosition:=", mm(xo), "YPosition:=", mm(yo), "ZPosition:=", mm(zo),
         "XSize:=",     mm(xs), "YSize:=",     mm(ys), "ZSize:=",     mm(zs)],
        ["NAME:Attributes",
         "Name:=",                  name,
         "Flags:=",                 flags,
         "Color:=",                 "(132 132 193)",
         "Transparency:=",          tr,
         "PartCoordinateSystem:=",  "Global",
         "UDMId:=",                 "",
         "MaterialValue:=",         '"' + mat + '"',
         "SurfaceMaterialValue:=",  '""',
         "SolveInside:=",           solve_inside,
         "ShellElement:=",          False,
         "ShellElementThickness:=", "0mm",
         "ReferenceTemperature:=",  "20cel",
         "IsMaterialEditable:=",    True,
         "UseMaterialAppearance:=", False,
         "IsLightweight:=",         False]
    )
    _log("  Box {}: ({:.3f},{:.3f},{:.4f})+({:.3f},{:.3f},{:.4f})".format(
        name, xo, yo, zo, xs, ys, zs))


def create_cylinder(oEditor, name, xc, yc, zbot, r, h, mat):
    solve_inside = mat.lower() not in _CONDUCTORS
    oEditor.CreateCylinder(
        ["NAME:CylinderParameters",
         "XCenter:=", mm(xc), "YCenter:=", mm(yc), "ZCenter:=", mm(zbot),
         "Radius:=",  mm(r),  "Height:=",  mm(h),  "WhichAxis:=", "Z",
         "NumSides:=", "0"],
        ["NAME:Attributes",
         "Name:=",                  name,
         "Flags:=",                 "",
         "Color:=",                 "(255 128 0)",
         "Transparency:=",          0,
         "PartCoordinateSystem:=",  "Global",
         "UDMId:=",                 "",
         "MaterialValue:=",         '"' + mat + '"',
         "SurfaceMaterialValue:=",  '""',
         "SolveInside:=",           solve_inside,
         "ShellElement:=",          False,
         "ShellElementThickness:=", "0mm",
         "ReferenceTemperature:=",  "20cel",
         "IsMaterialEditable:=",    True,
         "UseMaterialAppearance:=", False,
         "IsLightweight:=",         False]
    )


def create_circle(oEditor, name, x_face, yc, zc, r):
    """Circular disc in the YZ plane (WhichAxis=X) at x=x_face."""
    oEditor.CreateCircle(
        ["NAME:CircleParameters",
         "IsCovered:=", True,
         "XCenter:=",   mm(x_face),
         "YCenter:=",   mm(yc),
         "ZCenter:=",   mm(zc),
         "WhichAxis:=", "X",
         "Radius:=",    mm(r)],
        ["NAME:Attributes",
         "Name:=",                  name,
         "Flags:=",                 "",
         "Color:=",                 "(0 0 255)",
         "Transparency:=",          0,
         "PartCoordinateSystem:=",  "Global",
         "UDMId:=",                 "",
         "MaterialValue:=",         '"vacuum"',
         "SurfaceMaterialValue:=",  '""',
         "SolveInside:=",           True,
         "ShellElement:=",          False,
         "ShellElementThickness:=", "0mm",
         "ReferenceTemperature:=",  "20cel",
         "IsMaterialEditable:=",    True,
         "UseMaterialAppearance:=", False,
         "IsLightweight:=",         False]
    )


def create_rectangle(oEditor, name, axis, x, y, z, w, h):
    oEditor.CreateRectangle(
        ["NAME:RectangleParameters",
         "IsCovered:=", True,
         "XStart:=", mm(x), "YStart:=", mm(y), "ZStart:=", mm(z),
         "Width:=",  mm(w), "Height:=", mm(h), "WhichAxis:=", axis],
        ["NAME:Attributes",
         "Name:=",                  name,
         "Flags:=",                 "",
         "Color:=",                 "(255 0 0)",
         "Transparency:=",          0,
         "PartCoordinateSystem:=",  "Global",
         "UDMId:=",                 "",
         "MaterialValue:=",         '"vacuum"',
         "SurfaceMaterialValue:=",  '""',
         "SolveInside:=",           True,
         "ShellElement:=",          False,
         "ShellElementThickness:=", "0mm",
         "ReferenceTemperature:=",  "20cel",
         "IsMaterialEditable:=",    True,
         "UseMaterialAppearance:=", False,
         "IsLightweight:=",         False]
    )


def subtract_tool(oEditor, blanks, tools, keep=True):
    oEditor.Subtract(
        ["NAME:Selections",
         "Blank Parts:=", ",".join(blanks),
         "Tool Parts:=",  ",".join(tools)],
        ["NAME:SubtractParameters", "KeepOriginals:=", keep]
    )


# ===========================================================================
# Materials
# ===========================================================================

def add_material(oProject, name, er, tand):
    try:
        oDM = oProject.GetDefinitionManager()
        oDM.AddMaterial(
            ["NAME:" + name,
             "CoordinateSystemType:=",    "Cartesian",
             "BulkOrSurfaceType:=",       1,
             "permittivity:=",            str(float(er)),
             "dielectric_loss_tangent:=", str(float(tand))]
        )
        _log("  Material added: {} (er={}, tand={})".format(name, er, tand))
    except Exception as e:
        msg = str(e).lower()
        if "already" in msg or "exist" in msg or "duplicate" in msg:
            _log("  Material exists (ok): " + name)
        else:
            _warn("  AddMaterial {}: {}".format(name, str(e)))


# ===========================================================================
# PCB builder (simple GCPW — no compensation structures)
# ===========================================================================

def build_gcpw_pcb(oEditor, oProject, stackup,
                   length, width, trace_w, gap, solder_mask):
    add_material(oProject, "FR4_er45_tand20", ER_FR4, TAND_FR4)
    if solder_mask:
        add_material(oProject, "LPI_SolderMask", ER_SM, TAND_SM)

    hx  = length / 2.0
    hy  = width  / 2.0
    htw = trace_w / 2.0
    z   = 0.0
    out = {
        "copper_objects":     [],
        "dielectric_objects": [],
        "plane_objects":      [],
        "gcpw_gnd_objects":   [],
        "via_objects":        [],
        "trace_object":       "Trace_FCu",
    }

    for lname, lthick, ltype in stackup:
        if ltype == "copper":
            if lname == "F.Cu":
                out["z_fcu_bot"] = z
                gnd_w = hy - htw - gap
                create_box(oEditor, "Trace_FCu",
                           -hx, -htw, z, length, trace_w, lthick, "copper")
                out["copper_objects"].append("Trace_FCu")
                if gnd_w > 0.001:
                    create_box(oEditor, "CGND_FCu_PosY",
                               -hx, htw + gap, z, length, gnd_w, lthick, "copper")
                    out["copper_objects"].append("CGND_FCu_PosY")
                    out["gcpw_gnd_objects"].append("CGND_FCu_PosY")
                    create_box(oEditor, "CGND_FCu_NegY",
                               -hx, -hy, z, length, gnd_w, lthick, "copper")
                    out["copper_objects"].append("CGND_FCu_NegY")
                    out["gcpw_gnd_objects"].append("CGND_FCu_NegY")
            else:
                if lname == "B.Cu":
                    out["z_bcu_bot"] = z
                pname = "Plane_" + lname.replace(".", "_")
                create_box(oEditor, pname, -hx, -hy, z,
                           length, width, lthick, "copper")
                out["copper_objects"].append(pname)
                out["plane_objects"].append(pname)
                if lname == "B.Cu":
                    out["z_bcu_top"] = z + lthick
        else:
            sname = "Sub_" + lname
            create_box(oEditor, sname, -hx, -hy, z,
                       length, width, lthick, "FR4_er45_tand20")
            out["dielectric_objects"].append(sname)
        z += lthick

    out["z_fcu_top"]       = z
    out["board_thickness"] = z

    if solder_mask:
        sm_half = htw + gap + 0.075
        z_sm    = z
        z_bot   = out["z_bcu_bot"] - _SM_T
        gnd_sm  = hy - sm_half
        if gnd_sm > 0.001:
            create_box(oEditor, "SM_Top_PosY", -hx, sm_half, z_sm,
                       length, gnd_sm, _SM_T, "LPI_SolderMask")
            create_box(oEditor, "SM_Top_NegY", -hx, -hy, z_sm,
                       length, gnd_sm, _SM_T, "LPI_SolderMask")
        create_box(oEditor, "SM_Bot", -hx, -hy, z_bot,
                   length, width, _SM_T, "LPI_SolderMask")

    return out


# ===========================================================================
# Via fence
# ===========================================================================

def make_via_fence(oEditor, pcb, trace_w, gap, via_drill, via_pitch, via_clearance):
    hx    = BOARD_LENGTH / 2.0
    htw   = trace_w / 2.0
    via_r = via_drill / 2.0
    hy    = BOARD_WIDTH / 2.0
    via_y = htw + gap + via_clearance + via_r

    if via_y + via_r > hy:
        _warn("  Via fence outside board width — skipping.")
        return []

    x0     = -(hx - via_pitch / 2.0)
    n_vias = int((2.0 * hx - via_pitch) / via_pitch) + 1
    x_pos  = [x0 + float(i) * via_pitch for i in range(n_vias)]

    z_bot = pcb["z_bcu_bot"]
    z_top = pcb["z_fcu_top"]
    via_h = z_top - z_bot

    _log("  Via fence: {:d}×2 vias  pitch={:.2f}mm  drill={:.2f}mm".format(
        n_vias, via_pitch, via_drill))

    via_names = []
    for side, yc in [("PY", via_y), ("NY", -via_y)]:
        for i, xc in enumerate(x_pos):
            vname = "Via_{0}_{1:03d}".format(side, i)
            try:
                create_cylinder(oEditor, vname, xc, yc, z_bot, via_r, via_h, "copper")
                via_names.append(vname)
            except Exception as e:
                _warn("  Via {}: {}".format(vname, str(e)))

    for diel in pcb.get("dielectric_objects", []):
        if via_names:
            try:
                subtract_tool(oEditor, [diel], via_names, keep=True)
            except Exception as e:
                _warn("  Subtract " + diel + ": " + str(e))

    for plane in pcb.get("plane_objects", []):
        if via_names:
            try:
                subtract_tool(oEditor, [plane], via_names, keep=True)
            except Exception as e:
                _warn("  Subtract " + plane + ": " + str(e))

    py_vias = [n for n in via_names if "_PY_" in n]
    ny_vias = [n for n in via_names if "_NY_" in n]
    for gnd in pcb.get("gcpw_gnd_objects", []):
        row = py_vias if "PosY" in gnd else ny_vias
        if row:
            try:
                subtract_tool(oEditor, [gnd], row, keep=True)
            except Exception as e:
                _warn("  Subtract " + gnd + ": " + str(e))

    pcb["via_objects"] = via_names
    pcb["copper_objects"].extend(via_names)
    _log("  Created {:d} via barrels".format(len(via_names)))
    return via_names


# ===========================================================================
# Transition-zone mesh boxes
# ===========================================================================

def make_transition_zone_left(oEditor, hx, z_sma, thx, thy, thz):
    """Mesh zone on the LEFT side only (right side has a load, not an SMA connector)."""
    bname = "MeshZone_Trans_Left"
    try:
        create_box(oEditor, bname,
                   -hx - thx, -thy, z_sma - thz,
                   2.0 * thx, 2.0 * thy, 2.0 * thz,
                   "vacuum", transparent=True, nonmodel=True)
        _log("  Mesh zone left: ±{}mm around x={:.1f}".format(thx, -hx))
        return [bname]
    except Exception as e:
        _warn("  Zone Left: {}".format(str(e)))
        return []


# ===========================================================================
# SMA connector body — parametric from Würth 60312202114509 datasheet
# ===========================================================================

def make_sma_wurth(oEditor, oProject, hx, z_sma):
    """
    Build the Würth 60312202114509 SMA connector body from HFSS 3D primitives.

    Square-cross-section approximation (all dimensions from datasheet):
      Outer conductor : hollow copper box, cross-section ±(OUTER_R + SHELL_T)
                        centred at (y=0, z=z_sma).  Extends from x_cable to
                        x=-hx-0.01mm (0.01mm short of PCB edge to avoid
                        geometry conflict with the coplanar GND strips).
      PTFE fill       : square PTFE box between outer and inner conductors.
                        Same X extent as outer conductor.
      Inner conductor : copper strip, TRACE_WIDTH wide × _OZ1 thick.
                        Extends 0.01mm past the outer conductor to make direct
                        contact with the GCPW signal trace at x=-hx.
                        Centred at y=0, z=z_sma (mid F.Cu layer).

    The inner strip is thinner than the Würth pin (Ø 0.76mm) for two reasons:
      1. Avoids geometric overlap with the 0.20mm coplanar GND gap.
      2. Provides perfect continuity with the GCPW trace at the PCB edge.
    The difference is subsumed into the transition region mismatch, which is
    what this script is tuned to minimise.

    Returns x_cable (outer face X coordinate for wave port placement).
    """
    add_material(oProject, "PTFE_SMA", ER_PTFE, TAND_PTFE)

    x_cable = -(hx + SMA_BODY_LEN)    # cable end (wave port face)
    x_pcb   = -hx                      # PCB edge
    x_body  = x_pcb - 0.01             # connector body ends 0.01mm before PCB edge

    OUTER = SMA_OUTER_R
    SHELL = SMA_SHELL_T
    INN_W = TRACE_WIDTH
    INN_H = _OZ1

    # ── Outer conductor (hollow copper square tube) ───────────────────────────
    # Create full solid box then subtract the interior.
    full_name = "SMA_Outer_Full"
    hole_name = "_SMA_Outer_Hole"
    try:
        create_box(oEditor, full_name,
                   x_cable, -(OUTER + SHELL), z_sma - (OUTER + SHELL),
                   SMA_BODY_LEN - 0.01,         # stop at x_body
                   2.0 * (OUTER + SHELL), 2.0 * (OUTER + SHELL),
                   "copper")
        create_box(oEditor, hole_name,
                   x_cable, -OUTER, z_sma - OUTER,
                   SMA_BODY_LEN - 0.01,
                   2.0 * OUTER, 2.0 * OUTER,
                   "vacuum")
        subtract_tool(oEditor, [full_name], [hole_name], keep=False)
        _log("  SMA outer conductor (hollow Cu box) built")
    except Exception as e:
        _err("  SMA outer conductor: " + str(e))

    # ── PTFE fill (solid, then hollowed for inner conductor) ──────────────────
    ptfe_name = "SMA_PTFE"
    try:
        create_box(oEditor, ptfe_name,
                   x_cable, -OUTER, z_sma - OUTER,
                   SMA_BODY_LEN - 0.01,
                   2.0 * OUTER, 2.0 * OUTER,
                   "PTFE_SMA")
        _log("  SMA PTFE fill box built")
    except Exception as e:
        _err("  SMA PTFE: " + str(e))

    # ── Inner conductor (copper strip — continues as GCPW trace into PCB) ────
    # Extends 0.01mm past the connector body to x=-hx (PCB edge contact).
    inner_name = "SMA_Inner"
    try:
        create_box(oEditor, inner_name,
                   x_cable, -INN_W / 2.0, z_sma - INN_H / 2.0,
                   SMA_BODY_LEN,          # full length to x=-hx
                   INN_W, INN_H,
                   "copper")
        _log("  SMA inner conductor strip ({:.3f}x{:.4f}mm) from x={:.2f} to x={:.2f}".format(
            INN_W, INN_H, x_cable, x_cable + SMA_BODY_LEN))
    except Exception as e:
        _err("  SMA inner: " + str(e))

    # ── Hollow PTFE for inner conductor ────────────────────────────────────────
    try:
        subtract_tool(oEditor, [ptfe_name], [inner_name], keep=True)
        _log("  PTFE hollowed for inner conductor")
    except Exception as e:
        _warn("  PTFE subtract: " + str(e))

    _log("  SMA Würth body: x=[{:.2f}, {:.2f}]mm  centre y=0 z={:.4f}mm".format(
        x_cable, x_pcb, z_sma))
    _log("  Wave port face: x={:.2f}mm".format(x_cable))
    return x_cable


# ===========================================================================
# Wave port — circular disc at cable face (left)
# ===========================================================================

def make_wave_port(oDesign, oEditor, x_cable, z_sma, disc_r):
    """
    Circular wave port WP1 at x=x_cable (outer cable face of SMA connector).

    Integration line from centre (inner conductor) to rim (outer conductor).
    CharImp=Zpi lets HFSS compute the actual port impedance from the field
    solution — the square outer conductor shifts it slightly from 50 Ω.

    If S11 sweeps show a standing-wave offset, normalise the port via the
    Port Impedance report and add de-embed length manually.
    """
    sheet = "Port_Sheet_WP1"
    try:
        create_circle(oEditor, sheet, x_cable, 0.0, z_sma, disc_r)
        _log("  WP1 disc at x={:.3f}mm  r={:.2f}mm".format(x_cable, disc_r))
    except Exception as e:
        _err("  WP1 disc: " + str(e))
        return

    try:
        oBdry = oDesign.GetModule("BoundarySetup")
        oBdry.AssignWavePort(
            ["NAME:WP1",
             "Objects:=",               [sheet],
             "NumModes:=",              1,
             "UseLineModeAlignment:=",  False,
             "DoDeembed:=",             False,
             "UseIntLine:=",            True,
             "Start:=",                 [mm(x_cable), "0mm",        mm(z_sma)],
             "End:=",                   [mm(x_cable), mm(disc_r),   mm(z_sma)],
             "CharImp:=",               "Zpi",
             "AlignmentGroup:=",        0,
             "RenormalizeAllTerminals:=", True,
             "ShowReporterFilter:=",    False,
             "ReporterFilter:=",        [True],
             "UseAnalyticAlignment:=",  False]
        )
        _log("  Wave port WP1 assigned  (CharImp=Zpi)")
    except Exception as e:
        _err("  AssignWavePort WP1: " + str(e))


# ===========================================================================
# Right-end 50 Ω load
# ===========================================================================

def make_right_end_termination(oDesign, oEditor, hx, pcb):
    """
    50 Ω impedance boundary at x=+hx (right board face).

    Terminates the guided GCPW mode so S11 at WP1 reflects only the
    SMA connector transition, not an open-circuit reflection.

    API: AssignImpedance — confirmed by AEDT 2026.1 script recorder 2026-08-13.
    """
    sheet  = "Load_Sheet_Right"
    z_bot  = pcb["z_bcu_bot"]
    z_top  = pcb["z_fcu_top"]
    hy     = BOARD_WIDTH / 2.0
    height = z_top - z_bot

    try:
        create_rectangle(oEditor, sheet, "X",
                         hx, -hy, z_bot, BOARD_WIDTH, height)
        _log("  Load sheet at x=+{:.1f}mm  Y:[{:.2f},{:.2f}]  Z:[{:.4f},{:.4f}]".format(
            hx, -hy, hy, z_bot, z_top))
    except Exception as e:
        _err("  Load sheet: " + str(e))
        return

    try:
        oBdry = oDesign.GetModule("BoundarySetup")
        oBdry.AssignImpedance(
            ["NAME:Load_50R",
             "Objects:=",        [sheet],
             "Resistance:=",     "50",
             "Reactance:=",      "0",
             "InfGroundPlane:=", False])
        _log("  50 Ω load assigned at right end")
    except Exception as e:
        _err("  AssignImpedance: " + str(e))
        _warn("  Manual: select Load_Sheet_Right > Assign Boundary > Impedance  R=50 X=0")


# ===========================================================================
# Mesh operations
# ===========================================================================

def assign_length_op(oMesh, op_name, obj_list, max_len_mm, refine_inside=True):
    try:
        oMesh.AssignLengthOp(
            ["NAME:" + op_name,
             "RefineInside:=",   refine_inside,
             "Enabled:=",        True,
             "Objects:=",        obj_list,
             "RestrictElem:=",   False,
             "NumMaxElem:=",     "1000",
             "RestrictLength:=", True,
             "MaxLength:=",      mm(max_len_mm)]
        )
        _log("  LengthOp {} -> {:d} objs  max={:.3f}mm".format(
            op_name, len(obj_list), max_len_mm))
    except Exception as e:
        _warn("  LengthOp {}: {}".format(op_name, str(e)))


def assign_skin_depth_op(oMesh, op_name, obj_list, skin_mm, surf_len_mm):
    try:
        oMesh.AssignSkinDepthOp(
            ["NAME:" + op_name,
             "Enabled:=",          True,
             "Objects:=",          obj_list,
             "RestrictElem:=",     False,
             "NumMaxElem:=",       "1000",
             "SkinDepth:=",        mm(skin_mm),
             "SurfTriMaxLength:=", mm(surf_len_mm),
             "NumLayers:=",        2]
        )
        _log("  SkinDepthOp {} -> {:d} objs".format(op_name, len(obj_list)))
    except Exception as e:
        _warn("  SkinDepthOp {}: {}".format(op_name, str(e)))


def assign_mesh(oDesign, pcb, zone_names, sma_objects):
    oMesh = oDesign.GetModule("MeshSetup")

    if zone_names:
        assign_length_op(oMesh, "Mesh_Transition", zone_names, MESH_TRANS_MM)

    assign_length_op(oMesh, "Mesh_Trace", [pcb["trace_object"]], MESH_TRACE_MM)

    if pcb["gcpw_gnd_objects"]:
        assign_length_op(oMesh, "Mesh_CoplanarGND",
                         pcb["gcpw_gnd_objects"], MESH_CGND_MM)

    if pcb["plane_objects"]:
        assign_length_op(oMesh, "Mesh_Planes",
                         pcb["plane_objects"], MESH_PLANE_MM)

    if pcb["via_objects"]:
        chunk    = 50
        via_list = pcb["via_objects"]
        for ci in range(0, len(via_list), chunk):
            seg = via_list[ci:ci + chunk]
            assign_length_op(oMesh, "Mesh_Vias_{:02d}".format(ci // chunk),
                             seg, MESH_VIA_MM)

    if sma_objects:
        assign_length_op(oMesh, "Mesh_SMA", sma_objects, MESH_TRANS_MM)

    assign_skin_depth_op(oMesh, "Mesh_Skin_Trace",
                         [pcb["trace_object"]], MESH_SKIN_MM, MESH_TRACE_MM)


# ===========================================================================
# Solution setup
# ===========================================================================

def create_solution_setup(oDesign):
    oAnal = oDesign.GetModule("AnalysisSetup")
    try:
        oAnal.InsertSetup(
            "HfssDriven",
            ["NAME:HFSS_Adaptive",
             "SolveType:=",              "Single",
             "Frequency:=",              str(F_ADAPT) + "GHz",
             "MaxDeltaE:=",              MAX_DELTA_S,
             "MaximumPasses:=",          MAX_PASSES,
             "MinimumPasses:=",          1,
             "MinimumConvergedPasses:=", 1,
             "PercentRefinement:=",      30,
             "IsEnabled:=",              True,
             ["NAME:MeshLink", "ImportMesh:=", False],
             "BasisOrder:=",             -1,
             "DoLambdaRefine:=",         True,
             "DoMaterialLambda:=",       True,
             "SetLambdaTarget:=",        False,
             "Target:=",                 0.3333,
             "UseMaxTetIncrease:=",      False,
             "PortAccuracy:=",           2,
             "UseABCOnPort:=",           False,
             "SetPortMinMaxTri:=",       False,
             "UseDomains:=",             False,
             "UseIterativeSolver:=",     False,
             "SaveRadFieldsOnly:=",      False,
             "SaveAnyFields:=",          True,
             "IESolverType:=",           "Auto",
             "LambdaTargetForIESolver:=", 0.15,
             "UseDefaultLambdaTgtForIESolver:=", True,
             "IE Solver Accuracy:=",     "Balanced",
             "InfiniteSphereSetup:=",    ""]
        )
        _log("  Setup HFSS_Adaptive at {:.1f}GHz  dS<{:.3f}  passes={:d}".format(
            F_ADAPT, MAX_DELTA_S, MAX_PASSES))
        _warn("  TIP: add {:.1f}GHz adaptive: Edit Setup > Multi-Frequency".format(F_ADAPT2))
    except Exception as e:
        _err("  InsertSetup: " + str(e))
        raise

    try:
        oAnal.InsertFrequencySweep(
            "HFSS_Adaptive",
            ["NAME:RF_Sweep",
             "IsEnabled:=",             True,
             "RangeType:=",             "LinearCount",
             "RangeStart:=",            str(F_START) + "GHz",
             "RangeEnd:=",              str(F_STOP)  + "GHz",
             "RangeCount:=",            F_POINTS,
             "Type:=",                  "Interpolating",
             "SaveFields:=",            False,
             "SaveRadFields:=",         False,
             "InterpTolerance:=",       0.5,
             "InterpMaxSolns:=",        250,
             "InterpMinSolns:=",        0,
             "InterpMinSubranges:=",    1,
             "ExtrapToDC:=",            True,
             "InterpUseS:=",            True,
             "InterpUsePortImped:=",    False,
             "InterpUsePropConst:=",    True,
             "UseDerivativeConvergence:=", False,
             "InterpDerivTolerance:=",  0.2,
             "UseFullBasis:=",          True,
             "EnforcePassivity:=",      True,
             "PassivityErrorTolerance:=", 0.0001,
             "EnforceCausality:=",      False]
        )
        _log("  RF_Sweep {:.3f}-{:.1f}GHz  {:d}pts  Interpolating+ExtrapToDC".format(
            F_START, F_STOP, F_POINTS))
    except Exception as e:
        _err("  InsertFrequencySweep: " + str(e))


# ===========================================================================
# Report
# ===========================================================================

def create_report(oDesign):
    try:
        oReport = oDesign.GetModule("ReportSetup")
        oReport.CreateReport(
            "S Parameters",
            "Modal Solution Data",
            "Rectangular Plot",
            "HFSS_Adaptive : RF_Sweep",
            ["Domain:=", "Sweep"],
            ["Freq:=", ["All"]],
            ["X Component:=", "Freq",
             "Y Component:=", ["dB(S(WP1,WP1))"]],
            []
        )
        _log("  Report: dB(S11) vs Freq created")
    except Exception as e:
        _warn("  CreateReport: " + str(e))


# ===========================================================================
# Main
# ===========================================================================

def main():
    _step("Environment")
    _log("Python: " + sys.version.split(" ")[0])

    stackup = _STACKUPS.get(N_LAYERS)
    if stackup is None:
        _err("N_LAYERS must be 2 or 4.  Got: " + str(N_LAYERS))
        return

    board_t     = sum(t for _, t, _ in stackup)
    hx          = BOARD_LENGTH / 2.0
    design_name = DESIGN_BASE + "_" + str(N_LAYERS) + "L"

    _log("Topology : GCPW {:d}L  {:.0f}x{:.0f}mm  t={:.4f}mm".format(
        N_LAYERS, BOARD_LENGTH, BOARD_WIDTH, board_t))
    _log("Connector: Würth 60312202114509  (parametric, no .a3dcomp)  body={:.0f}mm".format(
        SMA_BODY_LEN))
    _log("Trace W  : {:.3f}mm  Gap: {:.3f}mm".format(TRACE_WIDTH, GAP_MM))
    _log("Port     : WP1 at x={:.2f}mm  (cable face)".format(-(hx + SMA_BODY_LEN)))
    _log("Load     : 50 Ω at x=+{:.1f}mm".format(hx))

    # -- Project / Design --------------------------------------------------
    _step("Project and design")
    try:
        oProject = oDesktop.GetActiveProject()
        if oProject is None:
            oProject = oDesktop.NewProject()
            oProject.Rename(PROJECT_NAME, True)
    except Exception as e:
        _err("Cannot get project: " + str(e))
        return

    try:
        existing = list(oProject.GetTopDesignList())
        if design_name in existing:
            oProject.DeleteDesign(design_name)
            _log("Deleted existing design: " + design_name)
    except Exception:
        pass

    try:
        oDesign = oProject.InsertDesign("HFSS", design_name, "DrivenModal", "")
        _log("HFSS design: " + design_name)
    except Exception as e:
        _err("InsertDesign: " + str(e))
        return

    oEditor = oDesign.SetActiveEditor("3D Modeler")
    try:
        oEditor.SetModelUnits(["NAME:Units", "Units:=", "mm", "Rescale:=", False])
    except Exception as e:
        _warn("SetModelUnits: " + str(e))

    try:
        oDesign.SetDesignSettings(
            ["NAME:Design Settings Data",
             "Allow Material Override:=",  True,
             "Calculate Lossy Dielectrics:=", True,
             "Perform Minimal validation:=", False,
             "IgnoreNonFunctionalPads:=",  True])
    except Exception as e:
        _warn("SetDesignSettings: " + str(e))

    # -- PCB ---------------------------------------------------------------
    _step("GCPW PCB stackup")
    try:
        pcb = build_gcpw_pcb(oEditor, oProject, stackup,
                             BOARD_LENGTH, BOARD_WIDTH,
                             TRACE_WIDTH, GAP_MM, SOLDER_MASK)
    except Exception as e:
        _err("build_gcpw_pcb: " + str(e))
        _err(traceback.format_exc())
        return

    # SMA connector centre height: mid of F.Cu layer (same reference as opt script)
    z_sma = pcb["z_fcu_bot"] + _OZ1 / 2.0
    _log("Board thickness: {:.4f}mm  F.Cu: [{:.4f}, {:.4f}]  z_sma: {:.4f}".format(
        pcb["board_thickness"], pcb["z_fcu_bot"], pcb["z_fcu_top"], z_sma))

    # -- Via fence ---------------------------------------------------------
    _step("Via fence")
    try:
        make_via_fence(oEditor, pcb,
                       TRACE_WIDTH, GAP_MM,
                       VIA_DRILL_MM, VIA_PITCH_MM, VIA_CLEARANCE_MM)
    except Exception as e:
        _err("make_via_fence: " + str(e))

    # -- SMA connector body ------------------------------------------------
    _step("SMA connector (parametric)")
    try:
        x_cable = make_sma_wurth(oEditor, oProject, hx, z_sma)
    except Exception as e:
        _err("make_sma_wurth: " + str(e))
        x_cable = -(hx + SMA_BODY_LEN)

    sma_objects = ["SMA_Outer_Full", "SMA_PTFE", "SMA_Inner"]

    # -- Transition zone mesh box (left only) ------------------------------
    _step("Transition mesh zones")
    try:
        zone_names = make_transition_zone_left(oEditor, hx, z_sma,
                                               MESH_TRANS_HX, MESH_TRANS_HY,
                                               MESH_TRANS_HZ)
    except Exception as e:
        _warn("make_transition_zone_left: " + str(e))
        zone_names = []

    # -- Mesh --------------------------------------------------------------
    _step("Mesh operations")
    try:
        assign_mesh(oDesign, pcb, zone_names,
                    [o for o in sma_objects if o != "SMA_PTFE"])
    except Exception as e:
        _warn("assign_mesh: " + str(e))

    # -- Wave port (WP1 at cable face) -------------------------------------
    _step("Wave port WP1")
    try:
        make_wave_port(oDesign, oEditor, x_cable, z_sma, PORT_DISC_R)
    except Exception as e:
        _err("make_wave_port: " + str(e))

    # -- Right-end 50 Ohm load ---------------------------------------------
    _step("Right-end 50 Ohm load")
    try:
        make_right_end_termination(oDesign, oEditor, hx, pcb)
    except Exception as e:
        _err("make_right_end_termination: " + str(e))

    # -- Solution setup ----------------------------------------------------
    _step("Solution setup")
    try:
        create_solution_setup(oDesign)
    except Exception as e:
        _err("create_solution_setup: " + str(e))

    # -- Report ------------------------------------------------------------
    _step("Report")
    try:
        create_report(oDesign)
    except Exception as e:
        _warn("create_report: " + str(e))

    # -- Save --------------------------------------------------------------
    _step("Save")
    try:
        oProject.Save()
        _log("Project saved.")
    except Exception as e:
        _warn("Save: " + str(e))

    elapsed = time.time() - _t0
    _log("=" * 55)
    _log("DONE  {:.1f}s".format(elapsed))
    _log("Model: WP1(x={:.1f}) ─ SMA body ─ GCPW 20mm ─ 50Ω load(x={:.1f})".format(
        x_cable, hx))
    _log("")
    _log("After solve, check:")
    _log("  1. Port impedance ≈ 50 Ω  (Solutions > Port Z0 table)")
    _log("     If Z0 ≠ 50 Ω, re-normalise via port settings or adjust SMA_OUTER_R")
    _log("  2. S11 dip depth and bandwidth in S Parameters plot")
    _log("  3. Current on SMA_Inner flows into Trace_FCu (no gap at x=-{:.1f}mm)".format(hx))
    _log("")
    _log("To tune: change TRACE_WIDTH / GAP_MM in CONFIG → re-run script")
    _log("Target : S11 < -20 dB across 0.1-20 GHz")
    _log("=" * 55)


try:
    main()
except Exception as _ex:
    _err("FATAL: " + str(_ex))
    _err(traceback.format_exc())
