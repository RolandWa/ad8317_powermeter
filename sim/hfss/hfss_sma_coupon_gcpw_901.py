# -*- coding: utf-8 -*-
# Run from: AEDT  Tools > Run Script
# IronPython 2.7 / AEDT 2026.1  -  NO external packages required
"""
hfss_sma_coupon_gcpw_901.py  -  SMA coupon for Würth 60312202114509 edge connector (GCPW)

NOTE: file was originally created for 901-10003-2023 and updated to Würth
60312202114509 before first use.  Rename to hfss_sma_coupon_gcpw_wurth.py
if desired.

Connector: Würth Elektronik 60312202114509
  SMA PCB End Launch Jack, Round Post, WR-SMA, 1/4-36 UNS-2A
  PCB thickness: 1.6 mm  (our 4-layer stackup ≈ 1.59 mm — matches)
  Centre contact pin Ø: 0.76 mm (radius 0.38 mm)
  Pin insertion depth into PCB: ≈ 1.27 mm  (from A-A cross-section, datasheet rev.001 2018-03-19)
  Land pattern: 9.9×5.6 mm pads, 6.6 mm inner span
  Coaxial interface: standard 1/4-36 UNS-2A SMA (inner conductor Ø 1.27 mm → radius 0.635 mm)
  Datasheet: we-online.com/components/products/datasheet/60312202114509.pdf

3D component: download STEP from we-online.com for part 60312202114509,
  import into AEDT (Modeler > Import), save as 60312202114509.a3dcomp in the
  same directory as this script, then re-run.

Grounded Coplanar Waveguide on a 4-layer FR4 PCB.  Both ends carry the
Würth connector.  Short pin (1.27 mm) → BOARD_LENGTH = 20 mm is sufficient.

Two-port setup: WP1 (left cable face) → GCPW → WP2 (right cable face).
Goal: minimise S11 / S22 and verify S21 < 1 dB insertion loss across 0–20 GHz.

Tuning workflow:
  1. Run script → check S11, S21 in AEDT.
  2. Adjust TRACE_WIDTH and GAP_MM in CONFIG → re-run.
  3. Target: S11 < -20 dB and S21 > -1 dB across 0.1–20 GHz.
  4. Compare with SM-2400071 coupon results to assess Würth transition quality.

Body-name note: the 60312202114509.a3dcomp internal body names are unknown
before the first run.  This script uses post-insertion object-diff to detect
new bodies and apply Y→X rotations and right-side mirror at runtime.
Check the log for "New bodies" diagnostics on first use.
"""

import os
import sys
import time
import traceback

# ===========================================================================
# CONFIG
# ===========================================================================

SMA_COMP_FILE = "60312202114509.a3dcomp"

# Würth 60312202114509 centre contact (PCB pin) dimensions.
# These affect the footprint / trace geometry, NOT the coaxial wave port.
PCB_PIN_DIAMETER = 0.76   # mm  Ø of centre contact (from datasheet)
PCB_PIN_RADIUS   = 0.38   # mm  = PCB_PIN_DIAMETER / 2

# Pin insertion depth from connector body face into the PCB.
# From datasheet A-A cross-section (rev.001 2018-03-19): ≈ 1.27 mm.
# Verify against 3D model or physical measurement before final signoff.
CONTACT_LENGTH = 1.27   # mm

N_LAYERS    = 4
SOLDER_MASK = True

BOARD_LENGTH = 20.0   # mm  X  (standard coupon — 1.27 mm pins are short, 20 mm is ample)
BOARD_WIDTH  = 10.0   # mm  Y

# GCPW dimensions for 50-ohm target (mm)
# Compute W and G with TX-Line / AppCAD: er=4.5, h = dielectric below F.Cu
#   4-layer JLCPCB  h=0.2101mm  ->  W~0.35mm  G~0.20mm   <- default
#   2-layer         h=1.5300mm  ->  W~1.80mm  G~0.30mm
TRACE_WIDTH     = 0.35   # mm  signal conductor  ← tune first
GAP_MM          = 0.20   # mm  each side          ← tune second

# Via fence
VIA_DRILL_MM     = 0.30   # mm  barrel diameter
VIA_PITCH_MM     = 0.80   # mm  centre-to-centre along X
VIA_CLEARANCE_MM = 0.05   # mm  barrel wall to inner gap edge

# SMA coaxial port dimensions (standard SMA cable face)
PORT_OUTER_R = 3.50   # mm  outer conductor inner radius
PORT_INNER_R = 0.65   # mm  inner conductor outer radius

# Frequency
F_START     = 0.01    # GHz
F_STOP      = 20.0    # GHz
F_ADAPT     = 16.0    # GHz  primary adaptive frequency
F_ADAPT2    = 1.0     # GHz  add via Edit Setup > Multi-Frequency
F_POINTS    = 401
MAX_DELTA_S = 0.02
MAX_PASSES  = 10      # 20 caused SOLVER_OUT_OF_MEMORY; each pass adds ~30%

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

PROJECT_NAME = "SMA_Coupon_GCPW_Wurth"
DESIGN_BASE  = "SMA_Coupon_GCPW_Wurth"

# ===========================================================================
# STACKUP DATABASE  (identical to hfss_sma_coupon_gcpw.py)
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
    6: [
        ("B.Cu",   _OZ1,   "copper"),
        ("Pre3",   0.1000, "dielectric"),
        ("In4.Cu", _OZ05,  "copper"),
        ("Core2",  0.3000, "dielectric"),
        ("In3.Cu", _OZ05,  "copper"),
        ("Pre2",   0.2000, "dielectric"),
        ("In2.Cu", _OZ05,  "copper"),
        ("Core1",  0.3000, "dielectric"),
        ("In1.Cu", _OZ05,  "copper"),
        ("Pre1",   0.1000, "dielectric"),
        ("F.Cu",   _OZ1,   "copper"),
    ],
    8: [
        ("B.Cu",   _OZ1,   "copper"),
        ("Pre4",   0.1000, "dielectric"),
        ("In6.Cu", _OZ05,  "copper"),
        ("Core3",  0.2000, "dielectric"),
        ("In5.Cu", _OZ05,  "copper"),
        ("Pre3",   0.1000, "dielectric"),
        ("In4.Cu", _OZ05,  "copper"),
        ("Core2",  0.2000, "dielectric"),
        ("In3.Cu", _OZ05,  "copper"),
        ("Pre2",   0.1000, "dielectric"),
        ("In2.Cu", _OZ05,  "copper"),
        ("Core1",  0.2000, "dielectric"),
        ("In1.Cu", _OZ05,  "copper"),
        ("Pre1",   0.1000, "dielectric"),
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


def _warn(msg):
    _log(msg, 1)


def _err(msg):
    _log(msg, 2)


def mm(v):
    return "{:.6f}mm".format(float(v))


# ===========================================================================
# Geometry primitives
# ===========================================================================

_CONDUCTORS = {"copper", "pec", "aluminum", "gold", "silver", "tungsten"}


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
    tag = " [NonModel]" if nonmodel else ""
    _log("  Box {}: ({:.3f},{:.3f},{:.4f})+({:.3f},{:.3f},{:.4f}){}".format(
        name, xo, yo, zo, xs, ys, zs, tag))


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


def create_cs(oEditor, name, xo, yo, zo, xvx, xvy, xvz, yvy=1.0):
    oEditor.CreateRelativeCS(
        ["NAME:RelativeCSParameters",
         "Mode:=",      "Axis/Position",
         "OriginX:=",   mm(xo),  "OriginY:=", mm(yo),  "OriginZ:=", mm(zo),
         "XAxisXvec:=", mm(xvx), "XAxisYvec:=", mm(xvy), "XAxisZvec:=", mm(xvz),
         "YAxisXvec:=", "0mm",   "YAxisYvec:=", mm(yvy), "YAxisZvec:=", "0mm"],
        ["NAME:Attributes", "Name:=", name]
    )
    _log("  CS {}: ({:.1f},{:.1f},{:.4f}) xvec=({:.0f},{:.0f},{:.0f}) yvy={:.0f}".format(
        name, xo, yo, zo, xvx, xvy, xvz, yvy))


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
# PCB builder (GCPW)
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
                    _warn("  Board too narrow for coplanar GND strips")
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
        _warn("  Via fence falls outside board width - skipping.")
        return []

    x0     = -(hx - via_pitch / 2.0)
    n_vias = int((2.0 * hx - via_pitch) / via_pitch) + 1
    x_pos  = [x0 + float(i) * via_pitch for i in range(n_vias)]

    z_bot  = pcb["z_bcu_bot"]
    z_top  = pcb["z_fcu_top"]
    via_h  = z_top - z_bot

    _log("  Via fence: {:d} vias/row  pitch={:.2f}mm  drill={:.2f}mm".format(
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

    _log("  Created {:d} via barrels".format(len(via_names)))

    for diel in pcb.get("dielectric_objects", []):
        if via_names:
            try:
                subtract_tool(oEditor, [diel], via_names, keep=True)
                _log("  Holes punched in " + diel)
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
    return via_names


# ===========================================================================
# Transition-zone mesh boxes
# ===========================================================================

def make_transition_zones(oEditor, hx, z_pin, thx, thy, thz):
    zones = []
    z_lo  = z_pin - thz
    z_hi  = z_pin + thz
    for side, xc in [("Left", -hx), ("Right", hx)]:
        bname = "MeshZone_Trans_" + side
        try:
            create_box(oEditor, bname,
                       xc - thx, -thy, z_lo,
                       2.0 * thx, 2.0 * thy, z_hi - z_lo,
                       "vacuum", transparent=True, nonmodel=True)
            zones.append(bname)
        except Exception as e:
            _warn("  Zone {}: {}".format(bname, str(e)))
    return zones


# ===========================================================================
# Mesh
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


def assign_mesh(oDesign, pcb, zone_names):
    oMesh = oDesign.GetModule("MeshSetup")

    if zone_names:
        assign_length_op(oMesh, "Mesh_Transition", zone_names, MESH_TRANS_MM)
    assign_length_op(oMesh, "Mesh_Trace", [pcb["trace_object"]], MESH_TRACE_MM)
    if pcb["gcpw_gnd_objects"]:
        assign_length_op(oMesh, "Mesh_CoplnarGND", pcb["gcpw_gnd_objects"], MESH_CGND_MM)
    if pcb["plane_objects"]:
        assign_length_op(oMesh, "Mesh_Planes",    pcb["plane_objects"],    MESH_PLANE_MM)
    if pcb["via_objects"]:
        chunk    = 50
        via_list = pcb["via_objects"]
        for ci in range(0, len(via_list), chunk):
            seg = via_list[ci:ci + chunk]
            assign_length_op(oMesh, "Mesh_Vias_{:02d}".format(ci // chunk),
                             seg, MESH_VIA_MM)

    assign_skin_depth_op(oMesh, "Mesh_Skin_00",
                         [pcb["trace_object"]], MESH_SKIN_MM, MESH_TRACE_MM)


# ===========================================================================
# SMA placement  (901_10003_2023 — dynamic body detection)
# ===========================================================================

def _snapshot(oEditor):
    """Return the current set of all model object names."""
    try:
        return set(oEditor.GetMatchedObjectName("*"))
    except Exception:
        return set()


def _new_bodies(snap_before, snap_after):
    """Return sorted list of bodies added since snap_before."""
    return sorted(snap_after - snap_before)


def _main_body(bodies):
    """
    Heuristic: the connector enclosure body is the first body whose name does
    NOT contain common sub-part keywords (insulation, dielectric, shell, pin…).
    Falls back to the first body if nothing matches.
    """
    sub_kw = {"insulation", "dielectric", "shell", "pin", "air", "sub",
              "solder", "contact", "port", "seal"}
    for b in bodies:
        if not any(k in b.lower() for k in sub_kw):
            return b
    return bodies[0] if bodies else None


def place_sma(oEditor, side, comp_path, hx, z_pin):
    """
    Insert 901_10003_2023 SMA edge connector.

    Left side:  CS with xvec=(-1,0,0) places body, then Y+X rotations are
                applied (same correction required as for SM-2400071 in
                AEDT 2026.1 — recorded 2026-08-13).
    Right side: Mirror about x=+hx plane, identical to SM-2400071 approach.

    Body names are detected dynamically (diff before/after insertion).
    """
    if not os.path.exists(comp_path):
        _warn("  SMA comp not found: " + comp_path)
        return

    x_contact = -hx if side == "left" else hx
    comp_name = "SMA_" + side
    cs_name   = "CS_SMA_" + side
    xvx       = -1.0 if side == "left" else 1.0

    try:
        create_cs(oEditor, cs_name, x_contact, 0.0, z_pin, xvx, 0.0, 0.0)
    except Exception as e:
        _err("  CS {}: {}".format(cs_name, str(e)))
        return

    snap_before = _snapshot(oEditor)

    try:
        oEditor.Insert3DComponent(
            ["NAME:InsertComponentData",
             "TargetCS:=",               cs_name,
             "ComponentFile:=",          comp_path,
             "IsSelectable:=",           True,
             "ComponentName:=",          comp_name,
             "Rect:=",                   False,
             "RectWidth:=",              1.0,
             "IsDraftComponent:=",       False,
             "NoCompilationNecessary:=", True]
        )
        _log("  Inserted {} at x={:.1f}mm".format(comp_name, x_contact))
    except Exception as e:
        _err("  Insert3DComponent {}: {}".format(comp_name, str(e)))
        _warn("  Manual: Insert > 3D Component > {} at ({:.1f},0,{:.4f})mm".format(
            os.path.basename(comp_path), x_contact, z_pin))
        return

    snap_after = _snapshot(oEditor)
    new = _new_bodies(snap_before, snap_after)
    _log("  New bodies after insertion ({}): {}".format(side, new))

    main = _main_body(new)
    if not main:
        _warn("  Could not detect connector body — rotations/mirror skipped")
        return

    if side == "left":
        # AEDT 2026.1: TargetCS alone does not fully orient the SMA body.
        # Script recorder (2026-08-13) confirmed Y+X rotations on main body.
        for axis, angle in [("Y", "90deg"), ("X", "90deg")]:
            try:
                oEditor.Rotate(
                    ["NAME:Selections",
                     "Selections:=",        main,
                     "NewPartsModelFlag:=", "Model"],
                    ["NAME:RotateParameters",
                     "RotateAxis:=",  axis,
                     "RotateAngle:=", angle])
                _log("  {} rotated {}  {}".format(main, axis, angle))
            except Exception as e:
                _warn("  Rotate {} {}: {}".format(main, axis, str(e)))

    elif side == "right":
        # Mirror about x=hx so right connector faces +X (cable points right).
        def _try_mirror(target):
            try:
                oEditor.Mirror(
                    ["NAME:Selections",
                     "Selections:=",        target,
                     "NewPartsModelFlag:=", "Model"],
                    ["NAME:MirrorParameters",
                     "MirrorBaseX:=",   mm(hx),
                     "MirrorBaseY:=",   "0mm",
                     "MirrorBaseZ:=",   "0mm",
                     "MirrorNormalX:=", "1mm",
                     "MirrorNormalY:=", "0mm",
                     "MirrorNormalZ:=", "0mm"])
                _log("  SMA_right mirrored via '{}'".format(target))
                return True
            except Exception as e:
                _warn("  Mirror '{}': {}".format(target, str(e)[:80]))
                return False

        mirrored = _try_mirror(main)
        if not mirrored:
            for b in new:
                if _try_mirror(b):
                    mirrored = True
                    break
        if not mirrored:
            _warn("  SMA_right: mirror failed.")
            _warn("  Manual: select main body > Modeler > Mirror, "
                  "plane normal=X, base=({:.1f},0,0)".format(hx))


# ===========================================================================
# Wave ports  (WP1 = left cable face, WP2 = right cable face)
# ===========================================================================

def make_wave_ports(oDesign, oEditor, hx, z_pin, outer_r, inner_r):
    """
    Place coaxial wave ports at both outer (cable) faces of the SMA connectors.

    GetModelBoundingBox is called AFTER both connectors are placed:
      bbox[0] = x-min = outer face of LEFT connector  → WP1
      bbox[3] = x-max = outer face of RIGHT connector → WP2

    Fallback: x = -(hx + 13) and +(hx + 13) if BoundingBox call fails.
    """
    try:
        bbox    = oEditor.GetModelBoundingBox()
        x_left  = float(bbox[0])
        x_right = float(bbox[3])
        _log("  Model bbox x: [{:.4f}, {:.4f}]mm  (connector outer faces)".format(
            x_left, x_right))
    except Exception as e:
        _warn("  GetModelBoundingBox: " + str(e))
        x_left  = -(hx + CONTACT_LENGTH)
        x_right = +(hx + CONTACT_LENGTH)
        _log("  Using fallback port x: [{:.1f}, {:.1f}]mm".format(x_left, x_right))

    def _make_port(port_name, x_face):
        sheet = "Port_Sheet_" + port_name
        try:
            create_rectangle(oEditor, sheet, "X",
                             x_face,
                             0.0 - outer_r, z_pin - outer_r,
                             2.0 * outer_r, 2.0 * outer_r)
            _log("  Coaxial disc {} at x={:.4f}mm  r={:.2f}mm".format(
                sheet, x_face, outer_r))
        except Exception as e:
            _err("  Port disc {}: {}".format(sheet, str(e)))
            return

        try:
            oBdry = oDesign.GetModule("BoundarySetup")
            oBdry.AssignWavePort(
                ["NAME:" + port_name,
                 "Objects:=",                [sheet],
                 "NumModes:=",               1,
                 "UseLineModeAlignment:=",   False,
                 "DoDeembed:=",              False,
                 "UseIntLine:=",             True,
                 "Start:=",                  [mm(x_face), "0mm",        mm(z_pin)],
                 "End:=",                    [mm(x_face), mm(outer_r),  mm(z_pin)],
                 "CharImp:=",               "Zpi",
                 "AlignmentGroup:=",         0,
                 "RenormalizeAllTerminals:=", True,
                 "ShowReporterFilter:=",      False,
                 "ReporterFilter:=",          [True],
                 "UseAnalyticAlignment:=",    False])
            _log("  Wave port {} assigned  (x={:.4f}mm)".format(port_name, x_face))
        except Exception as e:
            _err("  AssignWavePort {}: {}".format(port_name, str(e)))

    _make_port("WP1", x_left)
    _make_port("WP2", x_right)


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
        _warn("  TIP: add {:.1f}GHz adaptive via Edit Setup > Multi-Frequency".format(
            F_ADAPT2))
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
        _log("  Sweep {:.3f}-{:.1f}GHz  {:d}pts  Interpolating+ExtrapToDC".format(
            F_START, F_STOP, F_POINTS))
    except Exception as e:
        _err("  InsertFrequencySweep: " + str(e))
        raise


# ===========================================================================
# S-parameter report
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
             "Y Component:=", ["dB(S(WP1,WP1))", "dB(S(WP2,WP2))",
                                "dB(S(WP2,WP1))"]],
            []
        )
        _log("  Report: S11 + S22 + S21 vs Freq")
    except Exception as e:
        _warn("  CreateReport: " + str(e))
        _warn("  Manual: Results > Create Modal Solution Data Report > Rectangular Plot")


# ===========================================================================
# Main
# ===========================================================================

def main():
    _step("Environment")
    _log("Python : " + sys.version.split(" ")[0])

    stackup = _STACKUPS.get(N_LAYERS)
    if stackup is None:
        _err("N_LAYERS must be 2, 4, 6, or 8.  Got: " + str(N_LAYERS))
        return

    board_t     = sum(t for _, t, _ in stackup)
    hx          = BOARD_LENGTH / 2.0
    design_name = DESIGN_BASE + "_" + str(N_LAYERS) + "L"

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except (NameError, TypeError):
        script_dir = os.getcwd()
    comp_path = os.path.join(script_dir, SMA_COMP_FILE)

    _log("Topology    : GCPW  {:d}-layer  {:.1f}x{:.1f}mm  t={:.4f}mm".format(
        N_LAYERS, BOARD_LENGTH, BOARD_WIDTH, board_t))
    _log("Connector   : {}  Würth 60312202114509 (pin Ø{:.2f}mm  contact={:.2f}mm)".format(
        SMA_COMP_FILE, PCB_PIN_DIAMETER, CONTACT_LENGTH))
    _log("Ports       : WP1 (left cable face) + WP2 (right cable face)")
    _log("Trace W     : {:.3f}mm   Gap: {:.3f}mm   (50-ohm GCPW target)".format(
        TRACE_WIDTH, GAP_MM))
    _log("SMA comp    : " + comp_path +
         ("  [OK]" if os.path.exists(comp_path) else "  [MISSING]"))

    # -- Project / Design --------------------------------------------------
    _step("Project and design")
    try:
        oProject = oDesktop.GetActiveProject()
        if oProject is None:
            oProject = oDesktop.NewProject()
            oProject.Rename(PROJECT_NAME, True)
            _log("New project: " + PROJECT_NAME)
        else:
            _log("Using project: " + oProject.GetName())
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
        _log("HFSS design: " + design_name + "  (DrivenModal)")
    except Exception as e:
        _err("InsertDesign: " + str(e))
        return

    oEditor = oDesign.SetActiveEditor("3D Modeler")
    try:
        oEditor.SetModelUnits(
            ["NAME:Units", "Units:=", "mm", "Rescale:=", False])
        _log("Model units: mm")
    except Exception as e:
        _warn("SetModelUnits: " + str(e))

    try:
        oDesign.SetDesignSettings(
            ["NAME:Design Settings Data",
             "Allow Material Override:=",          True,
             "Calculate Lossy Dielectrics:=",       True,
             "Perform Minimal validation:=",        False,
             "DCThickness:=",                       "0mm",
             "Export After Simulation:=",           False,
             "Allow Export:=",                      True,
             "EnabledObjects:=",                    [],
             "SmallVoidArea:=",                     "-1mm^2",
             "RemoveFloatingMetals:=",              False,
             "SkipIntersectionChecks:=",            False,
             "ValidationLevel:=",                   "Standard",
             "IgnoreUnclassifiedObjects:=",         False,
             "IgnoreNonFunctionalPads:=",           True])
        _log("  Design settings: lossy dielectrics ON")
    except Exception as e:
        _warn("  SetDesignSettings: " + str(e))

    # -- PCB geometry ------------------------------------------------------
    _step("GCPW PCB stackup")
    try:
        pcb = build_gcpw_pcb(oEditor, oProject, stackup,
                             BOARD_LENGTH, BOARD_WIDTH,
                             TRACE_WIDTH, GAP_MM, SOLDER_MASK)
    except Exception as e:
        _err("build_gcpw_pcb: " + str(e))
        _err(traceback.format_exc())
        return

    z_pin = pcb["z_fcu_bot"] + _OZ1 / 2.0
    _log("Board thickness : {:.4f}mm".format(pcb["board_thickness"]))
    _log("F.Cu range      : [{:.4f}, {:.4f}]mm".format(
        pcb["z_fcu_bot"], pcb["z_fcu_top"]))
    _log("SMA pin Z (mid) : {:.4f}mm".format(z_pin))

    # -- Via fence ---------------------------------------------------------
    _step("Via fence")
    try:
        via_names = make_via_fence(oEditor, pcb,
                                   TRACE_WIDTH, GAP_MM,
                                   VIA_DRILL_MM, VIA_PITCH_MM, VIA_CLEARANCE_MM)
        _log("Total vias: {:d}  ({:d} per row)".format(
            len(via_names), len(via_names) // 2 if via_names else 0))
    except Exception as e:
        _err("make_via_fence: " + str(e))
        via_names = []

    # -- Transition mesh zones ---------------------------------------------
    _step("Transition-zone mesh boxes")
    try:
        zone_names = make_transition_zones(oEditor, hx, z_pin,
                                           MESH_TRANS_HX, MESH_TRANS_HY,
                                           MESH_TRANS_HZ)
    except Exception as e:
        _warn("make_transition_zones: " + str(e))
        zone_names = []

    # -- Mesh --------------------------------------------------------------
    _step("Mesh operations")
    try:
        assign_mesh(oDesign, pcb, zone_names)
    except Exception as e:
        _warn("assign_mesh: " + str(e))

    # -- SMA components (both sides) ---------------------------------------
    _step("SMA 3D components")
    place_sma(oEditor, "left",  comp_path, hx, z_pin)
    place_sma(oEditor, "right", comp_path, hx, z_pin)

    # -- Wave ports --------------------------------------------------------
    _step("Wave ports")
    try:
        make_wave_ports(oDesign, oEditor, hx, z_pin, PORT_OUTER_R, PORT_INNER_R)
    except Exception as e:
        _err("make_wave_ports: " + str(e))

    # -- Solution setup ----------------------------------------------------
    _step("Solution setup")
    try:
        create_solution_setup(oDesign)
    except Exception as e:
        _err("create_solution_setup: " + str(e))

    # -- Reports -----------------------------------------------------------
    _step("Reports")
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
    _log("DONE  {:.1f}s   {:d} vias   {:d} copper objects".format(
        elapsed, len(via_names), len(pcb["copper_objects"])))
    _log("Model: WP1 — {:d}mm GCPW — WP2   (Würth pin={:.2f}mm each side)".format(
        int(BOARD_LENGTH), CONTACT_LENGTH))
    _log("Tuning workflow:")
    _log("  1. Verify 3D: WP1/WP2 discs at outer SMA cable faces")
    _log("  2. Verify: both connectors oriented correctly (cable away from board)")
    _log("  3. Analyze > HFSS_Adaptive (local first, then HPC cluster)")
    _log("  4. Check S Parameters report: target S11/S22 < -20 dB, S21 > -1 dB")
    _log("  5. Adjust TRACE_WIDTH / GAP_MM → re-run until target met")
    _log("  6. Add {:.1f}GHz adaptive: Edit Setup > Multi-Frequency".format(F_ADAPT2))
    _log("  7. Save best CONFIG values to hfss_sma_coupon_gcpw_901_opt.py")
    _log("=" * 55)


try:
    main()
except Exception as _ex:
    _err("FATAL: " + str(_ex))
    _err(traceback.format_exc())
