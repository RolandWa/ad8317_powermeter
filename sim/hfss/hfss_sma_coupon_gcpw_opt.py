# -*- coding: utf-8 -*-
# Run from: AEDT  Tools > Run Script
# IronPython 2.7 / AEDT 2026.1  -  NO external packages required
"""
hfss_sma_coupon_gcpw_opt.py  -  SMA edge-connector optimization coupon (GCPW)

Half-model (20 mm default): one SMA connector on the left + radiation boundary
on the right face.  Wave port WP1 is placed on the outer coaxial face of the
SMA connector (cable end, found via GetModelBoundingBox).  The SMA component's
internal port P1 (at the PCB-pin interface) is preserved and not modified.
WP1 and P1 are at different X positions and do not conflict.

Eight HFSS design variables are registered (all geometry uses AEDT expressions):
  Optimised (4): comp_induct_w, comp_induct_len, comp_cap_w, comp_cap_len
  Fixed (4):     void_l2_x, void_l2_y, void_l3_x, void_l3_y  (held at defaults)

OptimizationSetup1 (kDX ASO = Adaptive Single-Objective(Gradient)) targets
S(WP1,WP1) <= -23 dB over 0.01-20 GHz (RF_Sweep) for the 4 LC-network
variables.  ParametricSetup1 sweeps each variable independently (24 solves)
to check sensitivity before optimising.

F.Cu signal conductor (3 segments, half-model):
  [-hx, -(hx-ent)]              : Trace_FCu_L_Ent  w=comp_induct_w  (series L)
  [-(hx-ent), -(hx-ent-cap)]   : Trace_FCu_L_Cap  w=comp_cap_w     (shunt C)
  [-(hx-ent-cap), +hx]         : Trace_FCu_Ctr    w=TRACE_WIDTH    (50-ohm)
  ent = comp_induct_len,  cap = comp_cap_len

F.Cu cross-section (Y axis, centred at Y=0):
  -hy ... -(htw+gap) : CGND_FCu_NegY   (coplanar GND, full length)
  -(htw+gap) ... -htw: gap
  -htw ... +htw      : signal (3 segments)
  +htw ... (htw+gap) : gap
  (htw+gap) ... +hy  : CGND_FCu_PosY   (coplanar GND, full length)

Post-run manual corrections (discovered 2026-08-12, now scripted):
  - MeshZone_Trans_Right deleted: extends 3mm past PML face; not needed on the
    right end because there is no SMA connector there.
  - SMA orientation: SM-2400071 places correctly with xvec=(-1,0,0) CS; body
    2023R1_HRMG_300_468B1 outer face lands at x = -(hx+5)mm (GetModelBoundingBox).
    An earlier script revision required manual rotations (90 deg Y then 90 deg X)
    to correct a misorientation; those rotations are no longer needed.
  - WP1 (Port_Sheet_WP1) is placed by GetModelBoundingBox directly at the
    connector outer face; no manual repositioning needed with current CS setup.
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

BOARD_LENGTH = 20.0   # mm  X  half-model: one SMA + 20mm of GCPW + PML right
BOARD_WIDTH  = 10.0   # mm  Y

# GCPW dimensions for 50-ohm target (mm)
# Compute W and G with TX-Line / AppCAD: er=4.5, h = dielectric below F.Cu
#   4-layer JLCPCB  h=0.2101mm  ->  W~0.35mm  G~0.20mm   <- default
#   2-layer         h=1.5300mm  ->  W~1.80mm  G~0.30mm
#   6-layer         h=0.1000mm  ->  W~0.15mm  G~0.10mm
TRACE_WIDTH      = 0.35   # mm  signal conductor
GAP_MM           = 0.20   # mm  each side

# Via fence
VIA_DRILL_MM     = 0.30   # mm  barrel diameter
VIA_PITCH_MM     = 0.80   # mm  centre-to-centre along X
VIA_CLEARANCE_MM = 0.05   # mm  barrel wall to inner gap edge

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
MAX_PASSES  = 10   # 20 caused SOLVER_OUT_OF_MEMORY; each pass adds ~30% mesh

# Mesh
MESH_TRANS_HX  = 3.0
MESH_TRANS_HY  = 3.0
MESH_TRANS_HZ  = 2.0
MESH_TRANS_MM  = 0.15
MESH_TRACE_MM  = 0.12
MESH_CGND_MM   = 0.25
MESH_PLANE_MM  = 0.60
MESH_VIA_MM    = 0.10
MESH_SKIN_MM   = 0.10    # applied to signal trace only — not to planes (OOM fix)

# Materials
ER_FR4   = 4.5
TAND_FR4 = 0.02
ER_SM    = 3.5
TAND_SM  = 0.025

PROJECT_NAME = "SMA_Coupon_GCPW_Opt"
DESIGN_BASE  = "SMA_Coupon_GCPW_Opt"   # design name gets "_4L" appended

# ===========================================================================
# OPTIMIZATION VARIABLES  -  initial values; Optimetrics will sweep these
# ===========================================================================

# Series-inductive entrance section (narrow trace at connector pin contact).
# Constraint: COMP_INDUCT_W < TRACE_WIDTH  (narrower = more inductance)
COMP_INDUCT_W   = 0.25   # mm  trace width in the entrance section
COMP_INDUCT_LEN = 0.40   # mm  length in X (from board edge inward)

# Shunt-capacitive stub (widened pad between entrance and main trace).
# Constraint: COMP_CAP_W/2 < TRACE_WIDTH/2 + GAP_MM  (must not short to GND)
#   For TRACE_WIDTH=0.35 GAP=0.20: max COMP_CAP_W ~ 0.74mm
COMP_CAP_W      = 0.65   # mm  total Y width of the stub
COMP_CAP_LEN    = 0.25   # mm  length in X

# Inner-layer ground void -- In1.Cu (closest inner below F.Cu, user Layer 2).
VOID_L2_X       = 4.00   # mm  void length in X at left board end
VOID_L2_Y       = 2.50   # mm  void half-width in Y (centred at Y=0)

# Inner-layer ground void -- In2.Cu (next inner, user Layer 3).
VOID_L3_X       = 3.00   # mm  void length in X at left board end
VOID_L3_Y       = 2.00   # mm  void half-width in Y

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
    """Format a numeric value as an AEDT mm string, or pass through AEDT expression strings."""
    try:
        return "{:.6f}mm".format(float(v))
    except (TypeError, ValueError):
        return str(v)   # already an AEDT variable expression


def _fmtv(v):
    """Format a value for logging — numeric or expression string."""
    try:
        return "{:.4f}".format(float(v))
    except (TypeError, ValueError):
        s = str(v)
        return s if len(s) <= 18 else s[:15] + "..."


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
    _log("  Box {}: ({},{},{})+({}  ,{},{}){}".format(
        name,
        _fmtv(xo), _fmtv(yo), _fmtv(zo),
        _fmtv(xs),  _fmtv(ys), _fmtv(zs), tag))


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
    # Flags must be empty — NonModel# prevents wave port / boundary assignment.
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
# HFSS design variables
# ===========================================================================

def add_design_variable(oDesign, name, value_str):
    """Register a local HFSS design variable accessible by Optimetrics."""
    try:
        oDesign.ChangeProperty(
            ["NAME:AllTabs",
             ["NAME:LocalVariableTab",
              ["NAME:PropServers", "LocalVariables"],
              ["NAME:NewProps",
               ["NAME:" + name,
                "PropType:=", "VariableProp",
                "UserDef:=", True,
                "Value:=", value_str]]]])
        _log("  Design variable {}: {}".format(name, value_str))
    except Exception as e:
        _warn("  Variable {} ({}): {}".format(name, value_str, str(e)))


# ===========================================================================
# PCB builder (GCPW, parametric for optimization)
# ===========================================================================

def build_gcpw_pcb_opt(oEditor, oProject, stackup,
                        length, width, trace_w, gap, solder_mask,
                        comp_induct_w, comp_induct_len,
                        comp_cap_w, comp_cap_len,
                        void_l2_x, void_l2_y,
                        void_l3_x, void_l3_y):
    """
    Parametric PCB builder for single-SMA half-model optimization.

    F.Cu signal: 3 touching copper boxes (L_Ent + L_Cap + Ctr-to-right-edge).
    All 8 optimization dimensions are passed as AEDT variable expression strings
    to CreateBox so Optimetrics can update geometry without re-running the script.

    Python float arguments are used only for validation and logging of defaults.
    """
    add_material(oProject, "FR4_er45_tand20", ER_FR4, TAND_FR4)
    if solder_mask:
        add_material(oProject, "LPI_SolderMask", ER_SM, TAND_SM)

    hx  = length / 2.0
    hy  = width  / 2.0
    htw = trace_w / 2.0

    # Validate default values at script time (AEDT will enforce Optimetrics bounds).
    gnd_inner = htw + gap
    if comp_cap_w / 2.0 >= gnd_inner - 0.010:
        _warn("  COMP_CAP_W ({:.3f}mm) may overlap coplanar GND inner edge ({:.3f}mm).".format(
            comp_cap_w, gnd_inner * 2.0))
        _warn("  Reduce comp_cap_w variable in Optimetrics or increase GAP_MM.")

    x_induct_end = hx - comp_induct_len
    x_ctr_end    = hx - comp_induct_len - comp_cap_len

    if x_ctr_end <= 0.0:
        _warn("  Compensation sections exceed board half-length! Reduce LEN values.")

    _log("  Transition (left side): ent={:.2f}mm + cap={:.2f}mm".format(
        comp_induct_len, comp_cap_len))
    _log("  Inductive W={:.3f}mm (trace={:.3f}mm)".format(comp_induct_w, trace_w))
    _log("  Capacitive W={:.3f}mm (GND inner edge={:.3f}mm)".format(
        comp_cap_w, gnd_inner * 2.0))

    # Pre-build AEDT expression fragments from fixed numeric constants.
    # These are the parts that never change during Optimetrics sweeps.
    _neg_hx = mm(-hx)                  # e.g. "-10.000000mm"
    _two_hx = mm(2.0 * hx)             # e.g. "20.000000mm"

    z   = 0.0
    out = {
        "copper_objects":       [],
        "dielectric_objects":   [],
        "plane_objects":        [],
        "gcpw_gnd_objects":     [],
        "via_objects":          [],
        "trace_object":         "Trace_FCu_Ctr",
        "comp_trace_objects":   [],
        "inner_layer_voids":    {},
    }

    inner_layers = []   # (lname, pname, z_bot, lthick) bottom-to-top, F.Cu excluded

    for lname, lthick, ltype in stackup:
        if ltype == "copper":
            if lname == "F.Cu":
                out["z_fcu_bot"] = z
                gnd_w = hy - htw - gap

                # ----------------------------------------------------------
                # F.Cu signal conductor — 3 parametric boxes
                # Box positions use AEDT expressions so Optimetrics geometry
                # updates live without re-running the script.
                # ----------------------------------------------------------

                # 1) Inductive entrance: from -hx toward center
                #    xo = -hx (fixed), xs = comp_induct_len (variable)
                create_box(oEditor, "Trace_FCu_L_Ent",
                           _neg_hx,
                           "-comp_induct_w/2",
                           mm(z),
                           "comp_induct_len",
                           "comp_induct_w",
                           mm(lthick), "copper")
                out["comp_trace_objects"].append("Trace_FCu_L_Ent")

                # 2) Capacitive stub: from end-of-entrance toward center
                #    xo = -hx + comp_induct_len
                create_box(oEditor, "Trace_FCu_L_Cap",
                           _neg_hx + "+comp_induct_len",
                           "-comp_cap_w/2",
                           mm(z),
                           "comp_cap_len",
                           "comp_cap_w",
                           mm(lthick), "copper")
                out["comp_trace_objects"].append("Trace_FCu_L_Cap")

                # 3) Main 50-ohm section: from end-of-stub to right edge (+hx)
                #    xo = -hx + comp_induct_len + comp_cap_len
                #    xs = 2*hx - comp_induct_len - comp_cap_len
                if x_ctr_end > 0.001:
                    create_box(oEditor, "Trace_FCu_Ctr",
                               _neg_hx + "+comp_induct_len+comp_cap_len",
                               mm(-htw),
                               mm(z),
                               _two_hx + "-comp_induct_len-comp_cap_len",
                               mm(trace_w),
                               mm(lthick), "copper")
                    out["copper_objects"].append("Trace_FCu_Ctr")

                out["copper_objects"].extend(out["comp_trace_objects"])

                # Coplanar GND strips — fixed dimensions (not optimization vars)
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
                    _warn("  Board too narrow for coplanar GND — "
                          "increase BOARD_WIDTH or reduce GAP_MM/TRACE_WIDTH")
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
                    inner_layers.append((lname, pname, z, lthick))
        else:
            sname = "Sub_" + lname
            create_box(oEditor, sname, -hx, -hy, z,
                       length, width, lthick, "FR4_er45_tand20")
            out["dielectric_objects"].append(sname)
        z += lthick

    out["z_fcu_top"]       = z
    out["board_thickness"] = z

    # ----------------------------------------------------------
    # Inner-layer voids (left/SMA end only — no void at right/PML end)
    # void sizes use AEDT expression strings so Optimetrics can sweep them.
    # ----------------------------------------------------------
    void_specs = [
        (void_l2_x, void_l2_y, "L2", "void_l2_x", "void_l2_y"),
        (void_l3_x, void_l3_y, "L3", "void_l3_x", "void_l3_y"),
    ]
    _log("  Applying voids to {:d} inner Cu layers (left end only)".format(
        min(len(void_specs), len(inner_layers))))

    for idx, (vx, vy, vtag, vvar_x, vvar_y) in enumerate(void_specs):
        li = -(idx + 1)
        if abs(li) > len(inner_layers):
            _warn("  Not enough inner layers for void {} (need {:d}, have {:d})".format(
                vtag, abs(li), len(inner_layers)))
            continue
        if vx < 0.001 or vy < 0.001:
            _log("  Void {} skipped (zero default size)".format(vtag))
            continue

        lname, pname, z_lbot, lthick = inner_layers[li]
        _log("  Void {} on {} : dx={:.2f}mm dy={:.2f}mm (initial)".format(
            vtag, lname, vx, vy))

        void_left = "Void_{}_Left".format(vtag)
        try:
            # xo = -hx (fixed), xs = void variable
            # yo = -void_y (AEDT unary minus), ys = 2*void_y
            create_box(oEditor, void_left,
                       _neg_hx,
                       "-" + vvar_y,
                       mm(z_lbot),
                       vvar_x,
                       "2*" + vvar_y,
                       mm(lthick), "vacuum")
            subtract_tool(oEditor, [pname], [void_left], keep=False)
            _log("  Void {} subtracted from {}".format(vtag, pname))
            out["inner_layer_voids"][lname] = (z_lbot, lthick, vx, vy)
        except Exception as e:
            _warn("  Void {}: {}".format(vtag, str(e)))

    # Solder mask (fixed geometry — not optimization variable)
    if solder_mask:
        sm_half = htw + gap + 0.075
        z_sm    = out["z_fcu_top"]
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

    z_bot = pcb["z_bcu_bot"]
    z_top = pcb["z_fcu_top"]
    via_h = z_top - z_bot

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

    dielectrics = pcb.get("dielectric_objects", [])
    for diel in dielectrics:
        if not via_names:
            break
        try:
            subtract_tool(oEditor, [diel], via_names, keep=True)
        except Exception as e:
            _warn("  Subtract " + diel + ": " + str(e))

    # Punch matching holes through copper ground planes (all vias pass through
    # plane_objects; only the matching row passes through each CGND strip).
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
# Transition-zone mesh boxes (NonModel)
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
        _log("  SkinDepthOp {} -> {:d} objs".format(op_name, len(obj_list)))
    except Exception as e:
        _warn("  SkinDepthOp {}: {}".format(op_name, str(e)))


def assign_mesh(oDesign, pcb, zone_names):
    oMesh = oDesign.GetModule("MeshSetup")

    if zone_names:
        assign_length_op(oMesh, "Mesh_Transition", zone_names, MESH_TRANS_MM)

    assign_length_op(oMesh, "Mesh_Trace", [pcb["trace_object"]], MESH_TRACE_MM)

    if pcb.get("comp_trace_objects"):
        assign_length_op(oMesh, "Mesh_Trace_Comp",
                         pcb["comp_trace_objects"], MESH_TRACE_MM)

    if pcb["gcpw_gnd_objects"]:
        assign_length_op(oMesh, "Mesh_CoplnarGND",
                         pcb["gcpw_gnd_objects"], MESH_CGND_MM)

    if pcb["plane_objects"]:
        assign_length_op(oMesh, "Mesh_Planes",
                         pcb["plane_objects"], MESH_PLANE_MM)

    if pcb["via_objects"]:
        chunk = 50
        via_list = pcb["via_objects"]
        for ci in range(0, len(via_list), chunk):
            seg = via_list[ci:ci + chunk]
            assign_length_op(oMesh, "Mesh_Vias_{:02d}".format(ci // chunk),
                             seg, MESH_VIA_MM)

    # Skin-depth op on signal conductor only — NOT on planes or coplanar GND.
    # Inner planes (In1/In2/B.Cu) are large flat surfaces: at 0.15mm surface
    # triangle length a 20x10mm plane generates ~90k surface triangles × 2
    # skin-depth layers = ~180k tets per plane.  Three planes × 20 adaptive
    # passes caused SOLVER_OUT_OF_MEMORY (2026-08-12).  The return current on
    # inner planes is diffuse; HFSS adaptive refinement handles it correctly
    # without an explicit skin-depth op.
    trace_cu = [pcb["trace_object"]] + list(pcb.get("comp_trace_objects") or [])
    if trace_cu:
        for ci in range(0, len(trace_cu), 50):
            seg = trace_cu[ci:ci + 50]
            assign_skin_depth_op(oMesh,
                                 "Mesh_Skin_{:02d}".format(ci // 50),
                                 seg, MESH_SKIN_MM, MESH_TRACE_MM)


# ===========================================================================
# SMA placement
# ===========================================================================

def place_sma(oEditor, side, comp_path, hx, z_pin):
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

        mirrored = _try_mirror("2023R1_HRMG_300_468B2", "AEDT-body-B2")
        if not mirrored:
            _warn("  SMA_right: mirror failed — select body manually.")


# ===========================================================================
# PML / radiation boundary (right board end)
# ===========================================================================

def make_pml_termination(oDesign, oEditor, hx, pcb):
    """
    Radiation boundary on the right PCB cross-section face (x = +hx).
    Absorbs the propagating GCPW mode so only WP1 (left coaxial port) is
    excited, halving solve time per Optimetrics iteration.
    Residual GCPW reflection at the right face is ~-20 to -30 dB — dominated
    by the connector transition under optimisation (~10-20 dB larger).
    """
    sheet  = "PML_Sheet_Right"
    z_bot  = pcb["z_bcu_bot"]
    z_top  = pcb["z_fcu_top"]
    hy     = BOARD_WIDTH / 2.0
    height = z_top - z_bot

    try:
        create_rectangle(oEditor, sheet, "X",
                         hx, -hy, z_bot, BOARD_WIDTH, height)
        _log("  PML sheet at x=+{:.1f}mm  Y:[{:.2f},{:.2f}]  Z:[{:.4f},{:.4f}]".format(
            hx, -hy, hy, z_bot, z_top))
    except Exception as e:
        _err("  PML sheet: " + str(e))
        return

    try:
        oBdry = oDesign.GetModule("BoundarySetup")
        oBdry.AssignRadiation(
            ["NAME:Rad_PML_Right",
             "Objects:=",     [sheet],
             "IsFssReference:=", False,
             "IsForPML:=",    False])
        _log("  Radiation boundary Rad_PML_Right assigned (absorbs GCPW mode)")
    except Exception as e:
        _err("  AssignRadiation: " + str(e))
        _warn("  Manual: select PML_Sheet_Right > Assign Boundary > Radiation")


# ===========================================================================
# Wave port — circular coaxial disc at the outer SMA face (WP1)
# ===========================================================================

def make_wave_port_coaxial(oDesign, oEditor, port_name, hx, z_pin, outer_r):
    """
    Circular coaxial wave port at the outer cable face of the left SMA connector.

    Face X-coordinate is read from GetModelBoundingBox AFTER Insert3DComponent —
    the SM-2400071 body is the leftmost object when placed with xvec=(-1,0,0).
    Falls back to -(hx + 13 mm) if the API call fails.

    The SMA component's internal port P1 (at the PCB-pin interface, x = -hx)
    is at a different X position and is NOT modified or removed.
    WP1 (here) is at the coaxial cable end, well to the left of P1.

    NOTE: An earlier script revision placed WP1 before the SMA was correctly
    oriented, causing GetModelBoundingBox to return the wrong x_face.  That
    required two manual moves of Port_Sheet_WP1 in AEDT after the script ran
    (recorded 2026-08-12).  The current CS setup (xvec=(-1,0,0)) places the
    connector correctly so no post-run repositioning is needed.
    """
    sheet_name = "Port_Sheet_" + port_name

    # Locate the outer coaxial face from the model bounding box.
    # After Insert3DComponent the SMA body is the leftmost model object.
    try:
        bb = oEditor.GetModelBoundingBox()
        # AEDT may return floats or "NNNmm" strings depending on build.
        def _bb_float(v):
            s = str(v).strip()
            return float(s[:-2] if s.endswith("mm") else s)
        x_face = _bb_float(bb[0])
        _log("  SMA outer face at x={:.4f}mm  (GetModelBoundingBox)".format(x_face))
    except Exception as e:
        x_face = -(hx + 13.0)
        _warn("  GetModelBoundingBox: {} — fallback x={:.1f}mm".format(
            str(e)[:60], x_face))

    # Circular disc in the YZ plane (WhichAxis=X) at x_face.
    try:
        oEditor.CreateCircle(
            ["NAME:CircleParameters",
             "IsCovered:=",  True,
             "XCenter:=",    mm(x_face),
             "YCenter:=",    "0mm",
             "ZCenter:=",    mm(z_pin),
             "WhichAxis:=",  "X",
             "Radius:=",     mm(outer_r)],
            ["NAME:Attributes",
             "Name:=",                  sheet_name,
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
        _log("  Coaxial disc {} at x={:.4f}mm  r={:.2f}mm".format(
            sheet_name, x_face, outer_r))
    except Exception as e:
        _err("  CreateCircle ({}): {}".format(sheet_name, str(e)))
        return

    # Assign wave port to the disc.
    try:
        oBdry = oDesign.GetModule("BoundarySetup")
        oBdry.AssignWavePort(
            ["NAME:" + port_name,
             "Objects:=",               [sheet_name],
             "NumModes:=",              1,
             "UseLineModeAlignment:=",  False,
             "DoDeembed:=",             False,
             "UseIntLine:=",            False,
             "AlignmentGroup:=",        0,
             "CharImp:=",               "Zpi",
             "RenormalizeAllTerminals:=", True,
             "ShowReporterFilter:=",    False,
             "ReporterFilter:=",        [True],
             "UseAnalyticAlignment:=",  False]
        )
        _log("  Wave port {} assigned  (coaxial outer face x={:.4f}mm)".format(
            port_name, x_face))
    except Exception as e:
        _err("  AssignWavePort ({}): {}".format(port_name, str(e)))
        _warn("  Manual: select {} > Assign Boundary > Wave Port".format(sheet_name))


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
        _log("  Sweep {:.3f}-{:.1f}GHz  {:d}pts  Interpolating+ExtrapToDC".format(
            F_START, F_STOP, F_POINTS))
    except Exception as e:
        _err("  InsertFrequencySweep: " + str(e))
        _warn("  RF_Sweep not created — add manually: right-click HFSS_Adaptive > Add Sweep")


# ===========================================================================
# Optimetrics optimization setup
# ===========================================================================

def create_optimetrics(oDesign):
    """
    Create OptimizationSetup1 using the exact AEDT 2026.1 API format recorded
    2026-08-12 (Setup Optimization > Screening(Search-based)).

    Three-step sequence (as generated by the AEDT script recorder):
      1. InsertSetup  — create setup with empty "NAME:Variables" section
      2. ChangeProperty — mark the 4 LC vars as Included in this optimization
      3. EditSetup — write the actual variable bounds into the setup

    Optimizer : kDX ASO  (GUI: "Adaptive Single-Objective(Gradient)")
                API string confirmed by AEDT 2026.1 script recorder 2026-08-12.
                DXOptimizerOptionData: InitSamples=19, MaxEvaluations=68,
                ConvergenceTolerance=0.001, MaxCycles=10, ScreenSamples=400.
                Phase 1 — 19 initial surrogate samples (space-filling DOE).
                Phase 2 — up to 68 total HFSS evaluations.
    Goal      : dB(S(WP1,WP1)) <= -23 dB  over 0.01-20 GHz  (RF_Sweep)
    Variables : comp_induct_w/len, comp_cap_w/len  (4 LC params)
    Fixed     : void_l2_x/y, void_l3_x/y held at CONFIG defaults (not included)
    """
    oOpt = oDesign.GetModule("Optimetrics")

    # ---------------------------------------------------------------------------
    # Physical bounds (float mm).  All derived from CONFIG so they stay
    # consistent if TRACE_WIDTH / GAP_MM are adjusted.
    #
    # comp_induct_w  : narrower than TRACE_WIDTH → series inductance.
    #                  Min 0.05mm = JLC 4-layer limit.
    # comp_induct_len: longer = more inductance.  >1.5mm Z-mismatch dominates.
    # comp_cap_w     : must be wider than TRACE_WIDTH (shunt C) but must not
    #                  reach the coplanar GND (limit = TRACE_WIDTH + 2*GAP_MM).
    # comp_cap_len   : same 1.5mm ceiling.
    # ---------------------------------------------------------------------------
    iw_min = 0.05
    iw_max = TRACE_WIDTH - 0.05                    # 0.300mm
    il_min = 0.10
    il_max = 1.50
    cw_min = TRACE_WIDTH + 0.01                    # 0.360mm
    cw_max = TRACE_WIDTH + 2.0 * GAP_MM - 0.05    # 0.700mm
    cl_min = 0.10
    cl_max = 1.50

    def _mmf(v):
        return "{:.6f}mm".format(v)

    def _var(vmin_f, vmax_f):
        """Variable bound record in the AEDT 2026.1 EditSetup format."""
        vrange  = vmax_f - vmin_f
        minstep = _mmf(vrange / 100.0)
        maxstep = _mmf(vrange / 10.0)
        level   = "[{:.6f}: {:.6f}] mm".format(vmin_f, vmax_f)
        return ["i:=",    True,
                "int:=",  False,
                "Min:=",  _mmf(vmin_f),
                "Max:=",  _mmf(vmax_f),
                "MinStep:=",  minstep,
                "MaxStep:=",  maxstep,
                "MinFocus:=", _mmf(vmin_f),
                "MaxFocus:=", _mmf(vmax_f),
                "UseManufacturableValues:=", "false",
                "Level:=",    level]

    # Shared sub-arrays (identical in both InsertSetup and EditSetup)
    _sp = ["NAME:StartingPoint",
           "comp_cap_len:=",    _mmf(COMP_CAP_LEN),
           "comp_cap_w:=",      _mmf(COMP_CAP_W),
           "comp_induct_len:=", _mmf(COMP_INDUCT_LEN),
           "comp_induct_w:=",   _mmf(COMP_INDUCT_W)]

    # Values confirmed by AEDT 2026.1 script recorder (2026-08-12).
    # RelGradientTolerance=0 and MinNumIteration=10 are the API defaults;
    # the dialog's displayed "Number of Initial Samples: 17" is a UI hint
    # (the actual API InitSamples parameter is 19 — see DXOptimizerOptionData).
    _stop = ["NAME:AnalysisStopOptions",
             "StopForNumIteration:=",   True,
             "StopForElapsTime:=",      False,
             "StopForSlowImprovement:=", False,
             "StopForGrdTolerance:=",   False,
             "MaxNumIteration:=",       68,
             "MaxSolTimeInSec:=",       3600,
             "RelGradientTolerance:=",  0,
             "MinNumIteration:=",       10]

    # Generate discrete frequency list matching HFSS RF_Sweep interpolation.
    # AEDT embeds all sweep points in the goal Range so the cost function is
    # evaluated at every available frequency — confirmed by script recorder.
    _step_ghz = (F_STOP - F_START) / (F_POINTS - 1)
    _disc_vals = ",".join(
        "{:.6g}GHz".format(F_START + i * _step_ghz) for i in range(F_POINTS))

    _goal = ["NAME:Goals",
             ["NAME:Goal",
              "ReportType:=", "Modal Solution Data",
              "Solution:=",   "HFSS_Adaptive : RF_Sweep",
              ["NAME:SimValueContext", "Domain:=", "Sweep"],
              "Calculation:=", "dB(S(WP1,WP1))",
              "Name:=",        "dB(S(WP1,WP1))",
              ["NAME:Ranges",
               "Range:=", ["Var:=",            "Freq",
                           "Type:=",            "rd",
                           "Start:=",           "{:.6g}GHz".format(F_START),
                           "Stop:=",            "{:.6g}GHz".format(F_STOP),
                           "DiscreteValues:=",  _disc_vals]],
              "Condition:=",  "<=",
              ["NAME:GoalValue",
               "GoalValueType:=", "Independent",
               "Format:=",        "Real/Imag",
               "bG:=",            ["v:=", "[-23;]"]],
              "Weight:=", "[1;]"]]

    # DXOptimizerOptionData is shared between DX SCREENING and kDX ASO —
    # but the fields differ.  Values below are confirmed by AEDT recorder for
    # kDX ASO with n=4 variables and "Use Default Setting" checked.
    _tail = ["Acceptable_Cost:=",      0,
             "Noise:=",                0.0001,
             "UpdateDesign:=",         False,
             "UpdateIteration:=",      5,
             "KeepReportAxis:=",       True,
             "UpdateDesignWhenDone:=", True,
             ["NAME:DXOptimizerOptionData",
              "InitSamples:=",               19,
              "MaxEvaluations:=",            68,
              "ConvergenceTolerance:=",      0.001,
              "RandomSeed:=",                0,
              "MaxCycles:=",                10,
              "ScreenSamples:=",            400,
              "StartingPoints:=",           12,
              "MaxDomainReductions:=",      20,
              "PercentDomainReductions:=",   0.1,
              "RetainedDomainPerIteration:=", 40]]

    def _body(variables_block):
        return (["NAME:OptimizationSetup1",
                 "IsEnabled:=", True,
                 ["NAME:ProdOptiSetupDataV2",
                  "SaveFields:=",            False,
                  "CopyMesh:=",              False,
                  "SolveWithCopiedMeshOnly:=", True],
                 _sp,
                 "Optimizer:=",        "kDX ASO",
                 _stop,
                 "CostFuncNormType:=", "L2",
                 "PriorPSetup:=",      "",
                 "PreSolvePSetup:=",   True,
                 variables_block,
                 ["NAME:LCS"],
                 _goal]
                + _tail)

    # -----------------------------------------------------------------------
    # Step 1: InsertSetup — empty Variables section (AEDT requires this order)
    # -----------------------------------------------------------------------
    try:
        oOpt.InsertSetup("OptiOptimization", _body(["NAME:Variables"]))
        _log("  OptimizationSetup1 created  (kDX ASO = Adaptive Single-Objective)")
        _log("  InitSamples=19  MaxEvaluations=68  ConvergenceTol=0.001")
    except Exception as e:
        _warn("  OptimizationSetup1 InsertSetup: " + str(e))
        _warn("  Manual: Optimetrics > Add > Optimization > Adaptive Single-Objective(Gradient)")
        _warn("  Goal: dB(S(WP1,WP1)) <= -23  over 0.01-20 GHz  (RF_Sweep)")
        return

    # -----------------------------------------------------------------------
    # Step 2: ChangeProperty — mark 4 LC vars as Included in this optimization
    # -----------------------------------------------------------------------
    try:
        oDesign.ChangeProperty(
            ["NAME:AllTabs",
             ["NAME:LocalVariableTab",
              ["NAME:PropServers", "LocalVariables"],
              ["NAME:ChangedProps",
               ["NAME:comp_induct_w",
                ["NAME:Optimization", "Included:=", True]],
               ["NAME:comp_induct_len",
                ["NAME:Optimization", "Included:=", True]],
               ["NAME:comp_cap_w",
                ["NAME:Optimization", "Included:=", True]],
               ["NAME:comp_cap_len",
                ["NAME:Optimization", "Included:=", True]]]]])
        _log("  4 LC variables marked Included in OptimizationSetup1")
    except Exception as e:
        _warn("  ChangeProperty (Optimization Included): " + str(e))

    # -----------------------------------------------------------------------
    # Step 3: EditSetup — write variable bounds (alphabetical order)
    # -----------------------------------------------------------------------
    try:
        vars_block = ["NAME:Variables",
                      "comp_cap_len:=",    _var(cl_min,  cl_max),
                      "comp_cap_w:=",      _var(cw_min,  cw_max),
                      "comp_induct_len:=", _var(il_min,  il_max),
                      "comp_induct_w:=",   _var(iw_min,  iw_max)]
        oOpt.EditSetup("OptimizationSetup1", _body(vars_block))
        _log("  OptimizationSetup1 bounds set:")
        _log("    comp_induct_w   [{:.3f}-{:.3f}] mm".format(iw_min, iw_max))
        _log("    comp_induct_len [{:.3f}-{:.3f}] mm".format(il_min, il_max))
        _log("    comp_cap_w      [{:.3f}-{:.3f}] mm".format(cw_min, cw_max))
        _log("    comp_cap_len    [{:.3f}-{:.3f}] mm".format(cl_min, cl_max))
        _log("  Goal : dB(S(WP1,WP1)) <= -23 dB  {:.4f}-{:.1f}GHz  (RF_Sweep)".format(
            F_START, F_STOP))
        _log("  (void_l2/l3 held at CONFIG defaults — not optimised)")
    except Exception as e:
        _warn("  OptimizationSetup1 EditSetup (bounds): " + str(e))
        _warn("  Manual: edit OptimizationSetup1 > Variables tab")


# ===========================================================================
# Parametric sweep — coarse 1-D sensitivity scan (ParametricSetup1)
# ===========================================================================

def create_parametric_sweep(oDesign):
    """
    ParametricSetup1: independent 1-D sweep of the 4 LC-network variables.
    Each variable is swept over its physical range in 6 steps while the
    others are held at their starting values (Synchronize=0).
    Total: 4 variables x 6 points = 24 adaptive solves.
    Void variables (void_l2/l3) are held at CONFIG defaults and not swept.
    Workflow: run ParametricSetup1 first to confirm LC sensitivity,
    then launch OptimizationSetup1 (DX SCREENING) to converge.
    """
    oOpt = oDesign.GetModule("Optimetrics")

    # Sweep ranges — same physical bounds as OptimizationSetup1, ~6 points each.
    def _sw(name, vmin, vmax, step):
        return ["NAME:SweepDefinition",
                "Variable:=",    name,
                "Data:=",        "LIN {} {} {}".format(vmin, vmax, step),
                "OffsetF1:=",    False,
                "Synchronize:=", 0]

    induct_w_max_f  = TRACE_WIDTH - 0.05
    cap_w_min_f     = TRACE_WIDTH + 0.01
    cap_w_max_f     = TRACE_WIDTH + 2.0 * GAP_MM - 0.05

    try:
        oOpt.InsertSetup(
            "OptiParametric",
            ["NAME:ParametricSetup1",
             "IsEnabled:=", True,
             ["NAME:ProdOptiSetupDataV2",
              "SaveFields:=",            False,
              "CopyMesh:=",              False,
              "SolveWithCopiedMeshOnly:=", True],
             ["NAME:StartingPoint"],
             "Sim. Setups:=", ["HFSS_Adaptive"],
             ["NAME:Sweeps",
              # comp_induct_w  0.05 → 0.30mm  step 0.05mm  (6 pts)
              _sw("comp_induct_w",
                  "{:.4f}mm".format(0.05),
                  "{:.4f}mm".format(induct_w_max_f),
                  "0.050mm"),
              # comp_induct_len  0.10 → 1.50mm  step 0.28mm  (6 pts)
              _sw("comp_induct_len", "0.100mm", "1.500mm", "0.280mm"),
              # comp_cap_w  lower → upper  step ~0.068mm  (6 pts)
              _sw("comp_cap_w",
                  "{:.4f}mm".format(cap_w_min_f),
                  "{:.4f}mm".format(cap_w_max_f),
                  "{:.4f}mm".format((cap_w_max_f - cap_w_min_f) / 5.0)),
              # comp_cap_len  0.10 → 1.50mm  step 0.28mm  (6 pts)
              _sw("comp_cap_len", "0.100mm", "1.500mm", "0.280mm")],
             ["NAME:Sweep Operations"],
             ["NAME:Goals"]]
        )
        _log("  ParametricSetup1: 4 vars × 6 pts = 24 solves  (LC sensitivity scan)")
        _log("  Run before OptimizationSetup1 to confirm LC parameter sensitivity.")
        _log("  (void_l2/l3 held fixed at CONFIG defaults)")
    except Exception as e:
        _warn("  ParametricSetup1 InsertSetup: " + str(e))
        _warn("  Manual: Optimetrics > Add > Parametric Setup")


# ===========================================================================
# Main
# ===========================================================================

def main():
    _step("Environment")
    _log("Python : " + sys.version.split(" ")[0])
    _log("Config (initial variable values):")
    _log("  comp_induct_w={:.3f}mm  comp_induct_len={:.3f}mm".format(
        COMP_INDUCT_W, COMP_INDUCT_LEN))
    _log("  comp_cap_w   ={:.3f}mm  comp_cap_len   ={:.3f}mm".format(
        COMP_CAP_W, COMP_CAP_LEN))
    _log("  void_l2: x={:.2f}mm  y={:.2f}mm".format(VOID_L2_X, VOID_L2_Y))
    _log("  void_l3: x={:.2f}mm  y={:.2f}mm".format(VOID_L3_X, VOID_L3_Y))

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

    _log("Topology    : GCPW_Opt  {:d}-layer  {:.1f}x{:.1f}mm  t={:.4f}mm".format(
        N_LAYERS, BOARD_LENGTH, BOARD_WIDTH, board_t))
    _log("Ports       : WP1 (circular, outer SMA face) + Rad_PML_Right (right end)")
    _log("Metric      : S(WP1,WP1) = connector return loss  [target < -23dB]")
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
             "Use Advanced DC Extrapolation:=",    False,
             "Use Power S:=",                      False,
             "Export FRTM After Simulation:=",     False,
             "Export Rays After Simulation:=",     False,
             "Export After Simulation:=",          False,
             "Allow Material Override:=",          True,
             "Calculate Lossy Dielectrics:=",      True,
             "Perform Minimal validation:=",       False,
             "EnabledObjects:=",                   [],
             "Port Validation Settings:=",         "Standard",
             "Save Adaptive support files:=",      False],
            ["NAME:Model Validation Settings",
             "EntityCheckLevel:=",                 "Basic",
             "IgnoreUnclassifiedObjects:=",        False,
             "SkipIntersectionChecks:=",           False])
        _log("Design settings: lossy dielectrics ON  material override ON  validation Standard")
    except Exception as e:
        _warn("SetDesignSettings: " + str(e))

    # -- Design variables (BEFORE geometry so expressions resolve) ---------
    _step("Design variables")
    _log("  Registering 8 design variables (4 LC optimised + 4 void fixed at defaults) ...")
    add_design_variable(oDesign, "comp_induct_w",
                        "{:.6f}mm".format(COMP_INDUCT_W))
    add_design_variable(oDesign, "comp_induct_len",
                        "{:.6f}mm".format(COMP_INDUCT_LEN))
    add_design_variable(oDesign, "comp_cap_w",
                        "{:.6f}mm".format(COMP_CAP_W))
    add_design_variable(oDesign, "comp_cap_len",
                        "{:.6f}mm".format(COMP_CAP_LEN))
    add_design_variable(oDesign, "void_l2_x",
                        "{:.6f}mm".format(VOID_L2_X))
    add_design_variable(oDesign, "void_l2_y",
                        "{:.6f}mm".format(VOID_L2_Y))
    add_design_variable(oDesign, "void_l3_x",
                        "{:.6f}mm".format(VOID_L3_X))
    add_design_variable(oDesign, "void_l3_y",
                        "{:.6f}mm".format(VOID_L3_Y))

    # -- PCB geometry ------------------------------------------------------
    _step("GCPW PCB stackup (parametric)")
    try:
        pcb = build_gcpw_pcb_opt(
            oEditor, oProject, stackup,
            BOARD_LENGTH, BOARD_WIDTH, TRACE_WIDTH, GAP_MM, SOLDER_MASK,
            COMP_INDUCT_W,   COMP_INDUCT_LEN,
            COMP_CAP_W,      COMP_CAP_LEN,
            VOID_L2_X,       VOID_L2_Y,
            VOID_L3_X,       VOID_L3_Y)
    except Exception as e:
        _err("build_gcpw_pcb_opt: " + str(e))
        _err(traceback.format_exc())
        return

    z_pin = pcb["z_fcu_bot"] + _OZ1 / 2.0
    _log("Board thickness : {:.4f}mm".format(pcb["board_thickness"]))
    _log("F.Cu range      : [{:.4f}, {:.4f}]mm".format(
        pcb["z_fcu_bot"], pcb["z_fcu_top"]))
    _log("SMA pin Z (mid) : {:.4f}mm".format(z_pin))
    _log("Inner voids applied: {}".format(sorted(pcb["inner_layer_voids"].keys())))

    # -- Via fence ---------------------------------------------------------
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
        via_names = []

    # -- Transition mesh zones ---------------------------------------------
    _step("Transition-zone mesh boxes")
    try:
        zone_names = make_transition_zones(oEditor, hx, z_pin,
                                           MESH_TRANS_HX, MESH_TRANS_HY,
                                           MESH_TRANS_HZ)
        # Half-model has SMA only on the LEFT; no connector on the right.
        # MeshZone_Trans_Right is unnecessary and also extends 3mm past the
        # PML face, which can trigger AEDT geometry warnings at solve time.
        # NOTE: discovered via manual AEDT inspection after the first script run.
        try:
            oEditor.Delete(["NAME:Selections",
                            "Selections:=",        "MeshZone_Trans_Right",
                            "NewPartsModelFlag:=", "Model"])
            zone_names = [z for z in zone_names if z != "MeshZone_Trans_Right"]
            _log("  Deleted MeshZone_Trans_Right (no right-side connector)")
        except Exception:
            pass
    except Exception as e:
        _warn("make_transition_zones: " + str(e))
        zone_names = []

    # -- Mesh --------------------------------------------------------------
    _step("Mesh operations")
    try:
        assign_mesh(oDesign, pcb, zone_names)
    except Exception as e:
        _warn("assign_mesh: " + str(e))

    # -- SMA component (left end only) -------------------------------------
    _step("SMA 3D component (left only)")
    place_sma(oEditor, "left", comp_path, hx, z_pin)

    # -- Ports -------------------------------------------------------------
    _step("Ports")
    # WP1: circular coaxial wave port at the outer face of the SMA connector.
    # Position determined from GetModelBoundingBox after Insert3DComponent.
    # The SMA component's internal port P1 (PCB-pin interface) is kept as-is.
    make_wave_port_coaxial(oDesign, oEditor, "WP1", hx, z_pin, PORT_OUTER_R)
    # Right end: radiation boundary absorbs propagating GCPW mode.
    make_pml_termination(oDesign, oEditor, hx, pcb)

    # -- Solution setup ----------------------------------------------------
    _step("Solution setup")
    try:
        create_solution_setup(oDesign)
    except Exception as e:
        _err("create_solution_setup: " + str(e))

    # -- Optimetrics -------------------------------------------------------
    _step("Optimetrics: OptimizationSetup1")
    try:
        create_optimetrics(oDesign)
    except Exception as e:
        _warn("create_optimetrics: " + str(e))

    # -- Parametric sweep (sensitivity scan) --------------------------------
    _step("Optimetrics: ParametricSetup1 (sensitivity scan)")
    try:
        create_parametric_sweep(oDesign)
    except Exception as e:
        _warn("create_parametric_sweep: " + str(e))

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
    _log("Model: WP1 (circular coax, outer SMA face) + 20mm GCPW + PML right")
    _log("Optimization workflow:")
    _log("  1. Verify: 3D view — check WP1 disc sits at SMA cable end (outer face)")
    _log("  2. Verify: In1.Cu / In2.Cu have voids at LEFT end only")
    _log("  3. Verify: AEDT Variables tab shows all 8 variables (4 fixed, 4 optimised)")
    _log("  4. Add {:.1f}GHz adaptive: Edit Setup > Multi-Frequency".format(F_ADAPT2))
    _log("  5. SENSITIVITY: Optimetrics > ParametricSetup1 > Analyze  (~24 solves)")
    _log("     — sweeps comp_induct_w/len + comp_cap_w/len independently")
    _log("  6. OPTIMISE: Optimetrics > OptimizationSetup1 > Analyze  (Quasi Newton)")
    _log("     — goal: dB(S(WP1,WP1)) <= -23 dB @ {:.0f} GHz".format(F_ADAPT))
    _log("  7. After convergence: run RF_Sweep to check broadband S11")
    _log("  8. Transfer best values to hfss_sma_coupon_gcpw.py (full 2-port model)")
    _log("=" * 55)


try:
    main()
except Exception as _ex:
    _err("FATAL: " + str(_ex))
    _err(traceback.format_exc())
