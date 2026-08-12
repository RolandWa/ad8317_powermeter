# -*- coding: utf-8 -*-
# Run from: AEDT  Tools > Run Script
# IronPython 2.7 / AEDT 2026.1  -  NO external packages required
"""
hfss_sma_coupon_gcpw.py  -  SMA edge-connector test coupon  (GCPW)

Grounded Coplanar Waveguide on a parametric 2-8 layer FR4 PCB coupon.
F.Cu carries:  coplanar GND strip | gap | signal trace | gap | coplanar GND strip
Through-hole via fence connects coplanar GNDs to all inner/bottom ground planes.
Wideband setup: 10 MHz to 20 GHz.

Run from inside AEDT:  Tools > Run Script > select this file.
Edit the CONFIG section before running.

F.Cu cross-section (Y axis, centred at Y=0):
  -hy ... -(htw+gap) : CGND_FCu_NegY (coplanar GND)
  -(htw+gap) ... -htw: gap (air / solder mask)
  -htw ... +htw      : Trace_FCu (signal)
  +htw ... (htw+gap) : gap
  (htw+gap) ... +hy  : CGND_FCu_PosY (coplanar GND)

Via fence (through-hole, full stack):
  Y = +/- (htw + gap + VIA_CLEARANCE + VIA_DRILL/2)
  X pitch: VIA_PITCH_MM  (keep < lambda_eff/10 at Fmax)
  At 20GHz in FR4 (er=4.5): lambda_eff ~ 7.07mm -> pitch < 0.71mm
"""

import os
import sys
import time
import traceback

# ===========================================================================
# CONFIG
# ===========================================================================

SMA_COMP_FILE = "SM-2400071.a3dcomp"

N_LAYERS    = 4
SOLDER_MASK = True

BOARD_LENGTH = 20.0   # mm  X
BOARD_WIDTH  = 10.0   # mm  Y

# GCPW dimensions for 50-ohm target (mm)
# Compute W and G with TX-Line / AppCAD: er=4.5, h = dielectric below F.Cu
#   4-layer JLCPCB  h=0.2101mm  ->  W~0.35mm  G~0.20mm   <- default
#   2-layer         h=1.5300mm  ->  W~1.80mm  G~0.30mm
#   6-layer         h=0.1000mm  ->  W~0.15mm  G~0.10mm
TRACE_WIDTH     = 0.35   # mm  signal conductor
GAP_MM          = 0.20   # mm  each side

# Via fence
VIA_DRILL_MM    = 0.30   # mm  barrel diameter
VIA_PITCH_MM    = 0.80   # mm  centre-to-centre along X
VIA_CLEARANCE_MM = 0.05  # mm  barrel wall to inner gap edge

# SMA port
PORT_OUTER_R = 3.50
PORT_INNER_R = 0.65

# Frequency
F_START     = 0.01
F_STOP      = 20.0
F_ADAPT     = 16.0
F_ADAPT2    = 1.0
F_POINTS    = 401
MAX_DELTA_S = 0.02
MAX_PASSES  = 20

# Mesh
MESH_TRANS_HX  = 3.0
MESH_TRANS_HY  = 3.0
MESH_TRANS_HZ  = 2.0
MESH_TRANS_MM  = 0.15    # transition zone max edge
MESH_TRACE_MM  = 0.12    # signal trace (TRACE_WIDTH/3 ~ 0.12 for W=0.35)
MESH_CGND_MM   = 0.25    # coplanar GND strips
MESH_PLANE_MM  = 0.60    # inner / B.Cu ground planes
MESH_VIA_MM    = 0.10    # via barrels
MESH_SKIN_MM   = 0.035

# Materials
ER_FR4   = 4.5
TAND_FR4 = 0.02
ER_SM    = 3.5
TAND_SM  = 0.025

PROJECT_NAME = "SMA_Coupon_GCPW"
DESIGN_BASE  = "SMA_Coupon_GCPW"

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
    tr = 0.6 if transparent else 0
    flags = "NonModel#" if nonmodel else ""
    # NonModel vacuum boxes still require SolveInside=True — AEDT rejects
    # vacuum objects with SolveInside=False regardless of the NonModel flag.
    solve_inside = mat.lower() not in _CONDUCTORS
    oEditor.CreateBox(
        ["NAME:BoxParameters",
         "XPosition:=", mm(xo),
         "YPosition:=", mm(yo),
         "ZPosition:=", mm(zo),
         "XSize:=",     mm(xs),
         "YSize:=",     mm(ys),
         "ZSize:=",     mm(zs)],
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
         "XCenter:=",  mm(xc),
         "YCenter:=",  mm(yc),
         "ZCenter:=",  mm(zbot),
         "Radius:=",   mm(r),
         "Height:=",   mm(h),
         "WhichAxis:=","Z",
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
    # Flags must be empty string - NonModel# prevents wave port assignment.
    oEditor.CreateRectangle(
        ["NAME:RectangleParameters",
         "IsCovered:=", True,
         "XStart:=",    mm(x),
         "YStart:=",    mm(y),
         "ZStart:=",    mm(z),
         "Width:=",     mm(w),
         "Height:=",    mm(h),
         "WhichAxis:=", axis],
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
        ["NAME:SubtractParameters",
         "KeepOriginals:=", keep]
    )


def create_cs(oEditor, name, xo, yo, zo, xvx, xvy, xvz, yvy=1.0):
    # yvy sets the Y-axis Y-component: +1 for left connector, -1 for right.
    # Left CS (xvx=-1, yvy=+1): Z = X cross Y = (0,0,-1) -> Z points DOWN.
    # Right CS (xvx=+1, yvy=-1): Z = X cross Y = (0,0,-1) -> Z points DOWN too.
    # Both connectors share the same Z orientation (no upside-down insertion).
    oEditor.CreateRelativeCS(
        ["NAME:RelativeCSParameters",
         "Mode:=",      "Axis/Position",
         "OriginX:=",   mm(xo),
         "OriginY:=",   mm(yo),
         "OriginZ:=",   mm(zo),
         "XAxisXvec:=", mm(xvx),
         "XAxisYvec:=", mm(xvy),
         "XAxisZvec:=", mm(xvz),
         "YAxisXvec:=", "0mm",
         "YAxisYvec:=", mm(yvy),
         "YAxisZvec:=", "0mm"],
        ["NAME:Attributes",
         "Name:=", name]
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
    """
    F.Cu = three copper objects:
      Trace_FCu       signal conductor (centre)
      CGND_FCu_PosY   coplanar GND +Y side
      CGND_FCu_NegY   coplanar GND -Y side
    All other layers: full-width copper ground planes.
    Returns geometry dict with object name lists and Z coordinates.
    """
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
                gnd_w = hy - htw - gap   # width of each coplanar GND strip

                # signal trace
                create_box(oEditor, "Trace_FCu",
                           -hx, -htw, z, length, trace_w, lthick, "copper")
                out["copper_objects"].append("Trace_FCu")

                if gnd_w > 0.001:
                    # coplanar GND +Y
                    create_box(oEditor, "CGND_FCu_PosY",
                               -hx, htw + gap, z, length, gnd_w, lthick, "copper")
                    out["copper_objects"].append("CGND_FCu_PosY")
                    out["gcpw_gnd_objects"].append("CGND_FCu_PosY")
                    # coplanar GND -Y
                    create_box(oEditor, "CGND_FCu_NegY",
                               -hx, -hy, z, length, gnd_w, lthick, "copper")
                    out["copper_objects"].append("CGND_FCu_NegY")
                    out["gcpw_gnd_objects"].append("CGND_FCu_NegY")
                else:
                    _warn("  Board too narrow for coplanar GND - "
                          "increase BOARD_WIDTH_MM or reduce GAP_MM/TRACE_WIDTH")
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
        # Opening spans trace + both gaps + 0.075mm clearance each side
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

def make_via_fence(oEditor, pcb, trace_w, gap, via_drill,
                   via_pitch, via_clearance):
    """
    Create through-hole via fence on both sides of the GCPW trace.

    Steps:
      1. Create copper barrel cylinders at all via positions (B.Cu -> F.Cu top).
      2. Subtract each barrel from every dielectric layer (keep_originals=True)
         so there is no conductor/dielectric overlap in the mesh.
      3. Subtract each barrel from every copper ground plane (plane_objects and
         gcpw_gnd_objects) so HFSS does not report via-plane volume intersections.
    """
    hx    = BOARD_LENGTH / 2.0
    htw   = trace_w / 2.0
    via_r = via_drill / 2.0
    hy    = BOARD_WIDTH / 2.0

    # Via Y centre: barrel wall sits via_clearance inside the GND strip
    # (inner edge of barrel is via_clearance away from the inner gap edge)
    via_y = htw + gap + via_clearance + via_r

    if via_y + via_r > hy:
        _warn("  Via fence falls outside board width - skipping.")
        _warn("  Increase BOARD_WIDTH_MM or reduce GAP/VIA params.")
        return []

    # X positions: first via at pitch/2 inside board edge, then uniform pitch
    x0     = -(hx - via_pitch / 2.0)
    n_vias = int((2.0 * hx - via_pitch) / via_pitch) + 1
    x_pos  = [x0 + float(i) * via_pitch for i in range(n_vias)]

    z_bot = pcb["z_bcu_bot"]
    z_top = pcb["z_fcu_top"]
    via_h = z_top - z_bot

    _log("  Via fence: {:d} vias/row  pitch={:.2f}mm  drill={:.2f}mm".format(
        n_vias, via_pitch, via_drill))
    _log("  Via Y centres: +/-{:.3f}mm  Z [{:.4f}, {:.4f}]".format(
        via_y, z_bot, z_top))

    via_names = []
    for side, yc in [("PY", via_y), ("NY", -via_y)]:
        for i, xc in enumerate(x_pos):
            vname = "Via_{0}_{1:03d}".format(side, i)
            try:
                create_cylinder(oEditor, vname, xc, yc, z_bot, via_r, via_h, "copper")
                via_names.append(vname)
            except Exception as e:
                _warn("  Via {}: {}".format(vname, str(e)))

    _log("  Created {:d} via barrels ({:d} per row)".format(
        len(via_names), n_vias))

    # Punch matching holes through all dielectric layers
    dielectrics = pcb.get("dielectric_objects", [])
    _log("  Subtracting from {:d} dielectric layers ...".format(len(dielectrics)))
    for diel in dielectrics:
        if not via_names:
            break
        try:
            subtract_tool(oEditor, [diel], via_names, keep=True)
            _log("  Holes punched in " + diel)
        except Exception as e:
            _warn("  Subtract " + diel + ": " + str(e))

    # Punch matching holes through copper ground planes (all vias pass through
    # plane_objects; only the matching row passes through each CGND strip).
    for plane in pcb.get("plane_objects", []):
        if via_names:
            try:
                subtract_tool(oEditor, [plane], via_names, keep=True)
                _log("  Holes punched in " + plane)
            except Exception as e:
                _warn("  Subtract " + plane + ": " + str(e))
    py_vias = [n for n in via_names if "_PY_" in n]
    ny_vias = [n for n in via_names if "_NY_" in n]
    for gnd in pcb.get("gcpw_gnd_objects", []):
        row = py_vias if "PosY" in gnd else ny_vias
        if row:
            try:
                subtract_tool(oEditor, [gnd], row, keep=True)
                _log("  Holes punched in " + gnd)
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
    z_lo = z_pin - thz
    z_hi = z_pin + thz
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
        _log("  SkinDepthOp {} -> {:d} objs  skin={:.4f}mm".format(
            op_name, len(obj_list), skin_mm))
    except Exception as e:
        _warn("  SkinDepthOp {}: {}".format(op_name, str(e)))


def assign_mesh(oDesign, pcb, zone_names):
    oMesh = oDesign.GetModule("MeshSetup")
    _log("  Transition  : {:.3f} mm".format(MESH_TRANS_MM))
    _log("  Trace       : {:.3f} mm".format(MESH_TRACE_MM))
    _log("  Coplanar GND: {:.3f} mm".format(MESH_CGND_MM))
    _log("  GND planes  : {:.3f} mm".format(MESH_PLANE_MM))
    _log("  Via barrels : {:.3f} mm".format(MESH_VIA_MM))
    _log("  Skin depth  : {:.4f} mm".format(MESH_SKIN_MM))

    if zone_names:
        assign_length_op(oMesh, "Mesh_Transition", zone_names, MESH_TRANS_MM)

    assign_length_op(oMesh, "Mesh_Trace", [pcb["trace_object"]], MESH_TRACE_MM)

    if pcb["gcpw_gnd_objects"]:
        assign_length_op(oMesh, "Mesh_CoplnarGND",
                         pcb["gcpw_gnd_objects"], MESH_CGND_MM)

    if pcb["plane_objects"]:
        assign_length_op(oMesh, "Mesh_Planes",
                         pcb["plane_objects"], MESH_PLANE_MM)

    if pcb["via_objects"]:
        # Split via list if very long to avoid API string-length issues
        chunk = 50
        via_list = pcb["via_objects"]
        for ci in range(0, len(via_list), chunk):
            seg = via_list[ci:ci + chunk]
            assign_length_op(oMesh, "Mesh_Vias_{:02d}".format(ci // chunk),
                             seg, MESH_VIA_MM)

    all_cu = pcb["copper_objects"]
    if all_cu:
        # Skin depth on all copper: split into batches of 50
        for ci in range(0, len(all_cu), 50):
            seg = all_cu[ci:ci + 50]
            assign_skin_depth_op(oMesh,
                                 "Mesh_Skin_{:02d}".format(ci // 50),
                                 seg, MESH_SKIN_MM, MESH_TRANS_MM)


# ===========================================================================
# SMA placement
# ===========================================================================

def place_sma(oEditor, side, comp_path, hx, z_pin):
    """
    Insert an SMA edge-connector 3D component at the board end.

    Insert3DComponent in AEDT 2026.1 ignores the TargetCS rotation axes (only
    translates).  Both connectors land in global orientation; the right one is
    corrected post-insertion by mirroring about the plane x=hx.

    oEditor.Mirror on the outer enclosure ("2023R1_HRMG_300_468B2") is the
    correct operation: submodel parts (INSULATION2, RSHELL2, …) follow B2
    automatically (confirmed via record.py).  A 180-deg-Z rotation was tried
    earlier but also flips Y, producing the wrong connector orientation.
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

    # Pre-insertion snapshot for fallback diagnostic diff (right side only).
    snap_before_all = set()
    if side == "right":
        try:
            snap_before_all = set(oEditor.GetMatchedObjectName("*"))
        except Exception:
            pass

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

    if side == "right":
        # Mirror about the plane x=hx (normal = +X, base on board right edge).
        # Reflects each point (x,y,z) to (2*hx-x, y, z): flips cable axis from
        # -X to +X while leaving Y unchanged.  180-deg-Z rotation was used before
        # but ALSO flips Y, producing the wrong connector orientation.
        #
        # record.py (user-recorded AEDT macro) confirms:
        #  - submodel parts follow B2 automatically when B2 is mirrored.
        #  - oEditor.Mirror on B2 alone is sufficient.
        def _try_mirror(target, desc):
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
                _log("  SMA_right mirrored via {} '{}'".format(desc, target))
                return True
            except Exception as e:
                _warn("  Mirror ({}) '{}': {}".format(desc, target, str(e)[:80]))
                return False

        mirrored = False

        mirrored = _try_mirror("2023R1_HRMG_300_468B2", "AEDT-body-B2")

        if not mirrored:
            try:
                inst_names = list(oEditor.Get3DComponentInstanceNames())
                _log("  Get3DComponentInstanceNames -> {}".format(inst_names))
                for n in inst_names:
                    if _try_mirror(n, "discovered-instance"):
                        mirrored = True
                        break
            except Exception as e:
                _warn("  Get3DComponentInstanceNames: {}".format(str(e)[:60]))

        if not mirrored:
            try:
                snap_after = set(oEditor.GetMatchedObjectName("*"))
                new_parts = sorted(snap_after - snap_before_all)
                _log("  Submodel diff (diagnostic): {}".format(new_parts))
            except Exception:
                pass

        if not mirrored:
            _warn("  SMA_right: mirror failed.")
            _warn("  Manual: select '2023R1_HRMG_300_468B2' in AEDT model tree")
            _warn("  Modeler > Mirror, plane normal=X, base=({:.1f},0,0)".format(hx))


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
             "RangeEnd:=",              str(F_STOP) + "GHz",
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
        _log("  Sweep {:.3f}-{:.1f}GHz  {:d}pts  Interpolating".format(
            F_START, F_STOP, F_POINTS))
    except Exception as e:
        _err("  InsertFrequencySweep: " + str(e))
        raise


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
    _log("Trace W     : {:.3f}mm   Gap: {:.3f}mm   (50-ohm GCPW)".format(
        TRACE_WIDTH, GAP_MM))
    _log("Via fence   : drill={:.2f}mm  pitch={:.2f}mm  clearance={:.2f}mm".format(
        VIA_DRILL_MM, VIA_PITCH_MM, VIA_CLEARANCE_MM))
    _log("Sweep       : {:.3f}-{:.1f}GHz  {:d}pts".format(F_START, F_STOP, F_POINTS))
    _log("SMA comp    : " + comp_path +
         ("  [OK]" if os.path.exists(comp_path) else "  [MISSING]"))

    # -- Project / Design -------------------------------------------------------
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

    # -- PCB geometry ----------------------------------------------------------
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

    # -- Via fence -------------------------------------------------------------
    _step("Via fence")
    try:
        via_names = make_via_fence(oEditor, pcb,
                                   TRACE_WIDTH, GAP_MM,
                                   VIA_DRILL_MM, VIA_PITCH_MM,
                                   VIA_CLEARANCE_MM)
        _log("Total vias: {:d}  ({:d} per row)".format(
            len(via_names), len(via_names) // 2 if via_names else 0))
    except Exception as e:
        _err("make_via_fence: " + str(e))
        _err(traceback.format_exc())
        via_names = []

    # -- Transition mesh zones -------------------------------------------------
    _step("Transition-zone mesh boxes")
    try:
        zone_names = make_transition_zones(oEditor, hx, z_pin,
                                           MESH_TRANS_HX, MESH_TRANS_HY,
                                           MESH_TRANS_HZ)
    except Exception as e:
        _warn("make_transition_zones: " + str(e))
        zone_names = []

    # -- Mesh ------------------------------------------------------------------
    _step("Mesh operations")
    try:
        assign_mesh(oDesign, pcb, zone_names)
    except Exception as e:
        _warn("assign_mesh: " + str(e))

    # -- SMA components --------------------------------------------------------
    _step("SMA 3D components")
    place_sma(oEditor, "left",  comp_path, hx, z_pin)
    place_sma(oEditor, "right", comp_path, hx, z_pin)

    # -- Solution setup --------------------------------------------------------
    _step("Solution setup")
    try:
        create_solution_setup(oDesign)
    except Exception as e:
        _err("create_solution_setup: " + str(e))

    # -- Save ------------------------------------------------------------------
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
    _log("Next steps:")
    _log("  1. Check coplanar GND strips contact SMA outer conductor")
    _log("  2. Verify wave port integration lines (outer rim -> pin centre)")
    _log("  3. Add {:.1f}GHz adaptive in Edit Setup > Multi-Frequency".format(F_ADAPT2))
    _log("  4. Analyze > HFSS_Adaptive")
    _log("=" * 55)


try:
    main()
except Exception as _ex:
    _err("FATAL: " + str(_ex))
    _err(traceback.format_exc())
