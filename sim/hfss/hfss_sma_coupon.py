# -*- coding: utf-8 -*-
# Run from: AEDT  Tools > Run Script
# IronPython 2.7 / AEDT 2026.1  -  NO external packages required
"""
hfss_sma_coupon.py  -  SMA edge-connector test coupon  (Microstrip)

Creates a parametric 2-8 layer FR4 PCB coupon with a 50-ohm microstrip
signal trace on F.Cu and two SMA edge connectors imported as .a3dcomp
3D Ansys Components.  Wideband setup: 10 MHz to 20 GHz.

Run from inside AEDT:  Tools > Run Script > select this file.
All geometry is built in a new HFSS DrivenModal design.
Edit the CONFIG section before running.
"""

import os
import sys
import time
import traceback

# ===========================================================================
# CONFIG  -  edit these values before running
# ===========================================================================

# .a3dcomp file name (must sit in the same folder as this script).
# Available models:  SM-2400071.a3dcomp  SM-2400089.a3dcomp  901_10003_2023.a3dcomp
SMA_COMP_FILE = "SM-2400071.a3dcomp"

# Stackup: 2 | 4 | 6 | 8  layers
N_LAYERS    = 4
SOLDER_MASK = True   # True = add LPI solder mask on top and bottom

# Board outline (mm)
BOARD_LENGTH = 20.0   # X - long axis, spans both SMA bodies
BOARD_WIDTH  = 10.0   # Y

# Signal trace width for 50-ohm microstrip on F.Cu (mm)
# Use Saturn PCB Toolkit / AppCAD / TX-Line with:
#   er=4.5,  substrate height h (dielectric below F.Cu)
#   2-layer  h=1.53 mm  ->  W ~ 2.97 mm
#   4-layer  h=0.21 mm  ->  W ~ 0.19 mm   <- default
#   6-layer  h=0.10 mm  ->  W ~ 0.085 mm
#   8-layer  h=0.10 mm  ->  W ~ 0.085 mm
TRACE_WIDTH = 0.19   # mm

# SMA coaxial port geometry (mm)
# Standard SMA 50-ohm: outer conductor inner radius = 3.50 mm
PORT_OUTER_R = 3.50
PORT_INNER_R = 0.65   # centre pin radius (for reference)

# Wideband frequency sweep
F_START      = 0.01    # GHz  (10 MHz)
F_STOP       = 20.0    # GHz
F_ADAPT      = 16.0    # GHz  primary adaptive mesh point
F_ADAPT2     = 1.0     # GHz  secondary adaptive point (low-freq accuracy)
F_POINTS     = 401
MAX_DELTA_S  = 0.02    # convergence threshold
MAX_PASSES   = 20

# Transition-zone mesh boxes at each SMA-to-PCB pin contact
# Half-extents (mm) and max element edge length inside the zone (mm)
MESH_TRANS_HX = 3.0    # X half-extent
MESH_TRANS_HY = 3.0    # Y half-extent
MESH_TRANS_HZ = 2.0    # Z half-extent
MESH_TRANS_MM = 0.20   # max element edge inside transition zone

# Mesh on signal trace: at least 3 elements across the trace width
MESH_TRACE_MM = 0.063  # override if TRACE_WIDTH / 3 > 0.05

# Ground planes: coarser mesh is fine for large flat conductors
MESH_PLANE_MM = 0.60

# Skin-depth surface refinement on copper (effective at high frequency)
MESH_SKIN_MM  = 0.035  # mm  (35 um = copper skin depth at ~1 GHz)

# Materials
ER_FR4   = 4.5
TAND_FR4 = 0.02
ER_SM    = 3.5
TAND_SM  = 0.025

# HFSS project / design names
PROJECT_NAME = "SMA_Coupon_MS"
DESIGN_BASE  = "SMA_Coupon_MS"   # layer count appended automatically

# ===========================================================================
# STACKUP DATABASE  (bottom -> top, B.Cu first)
# (layer_name, thickness_mm, type)   type = "copper" | "dielectric"
# JLCPCB standard stackups
# ===========================================================================
_OZ1  = 0.035    # 1 oz  copper (35 um)
_OZ05 = 0.0175   # 0.5 oz copper (17.5 um)
_SM_T = 0.025    # LPI solder mask thickness

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
# Logging  (console + AEDT Message Manager)
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


# ===========================================================================
# Unit helper
# ===========================================================================

def mm(v):
    """Format a numeric value as a millimetre string for AEDT API calls."""
    return "{:.6f}mm".format(float(v))


# ===========================================================================
# Geometry primitives
# ===========================================================================

_CONDUCTORS = {"copper", "pec", "aluminum", "gold", "silver", "tungsten"}


def create_box(oEditor, name, xo, yo, zo, xs, ys, zs, mat,
               transparent=False, nonmodel=False):
    tr = 0.6 if transparent else 0
    # NonModel# flag: object is visible but excluded from the EM solver.
    # Used for mesh-refinement guide boxes — they must NOT be model solids
    # or HFSS raises intersection errors when they overlap PCB/component bodies.
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
         "Name:=",                   name,
         "Flags:=",                  flags,
         "Color:=",                  "(132 132 193)",
         "Transparency:=",           tr,
         "PartCoordinateSystem:=",   "Global",
         "UDMId:=",                  "",
         "MaterialValue:=",          '"' + mat + '"',
         "SurfaceMaterialValue:=",   '""',
         "SolveInside:=",            solve_inside,
         "ShellElement:=",           False,
         "ShellElementThickness:=",  "0mm",
         "ReferenceTemperature:=",   "20cel",
         "IsMaterialEditable:=",     True,
         "UseMaterialAppearance:=",  False,
         "IsLightweight:=",          False]
    )
    tag = " [NonModel]" if nonmodel else ""
    _log("  Box {}: ({:.3f},{:.3f},{:.4f}) + ({:.3f},{:.3f},{:.4f}) mat={}{}".format(
        name, xo, yo, zo, xs, ys, zs, mat, tag))


def create_rectangle(oEditor, name, axis, x, y, z, w, h):
    """axis = 'X' -> YZ plane  'Y' -> XZ plane  'Z' -> XY plane
    Flags must be empty (not NonModel#) so wave ports can be assigned."""
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
    _log("  Rect {}: axis={} ({:.3f},{:.3f},{:.4f}) {}x{}mm".format(
        name, axis, x, y, z, w, h))


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
    _log("  CS {}: origin=({:.1f},{:.1f},{:.4f}) xvec=({:.0f},{:.0f},{:.0f}) yvy={:.0f}".format(
        name, xo, yo, zo, xvx, xvy, xvz, yvy))


# ===========================================================================
# Materials
# ===========================================================================

def add_material(oProject, name, er, tand):
    # Do not check GetMaterialNames() - it is not available on ADispatchWrapper.
    # AddMaterial() is idempotent: if the material already exists AEDT raises an
    # exception that we catch and silently ignore.
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
# PCB builder  (Microstrip)
# ===========================================================================

def build_pcb(oEditor, oProject, stackup, length, width, trace_w, solder_mask):
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
    }

    for lname, lthick, ltype in stackup:
        if ltype == "copper":
            if lname == "F.Cu":
                out["z_fcu_bot"] = z
                create_box(oEditor, "Trace_FCu",
                           -hx, -htw, z, length, trace_w, lthick, "copper")
                out["copper_objects"].append("Trace_FCu")
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

    out["z_fcu_top"]      = z
    out["board_thickness"] = z

    if solder_mask:
        sm_gap = htw + 0.075    # solder-mask opening half-width
        z_sm   = z
        z_bot  = out["z_bcu_bot"] - _SM_T
        if hy - sm_gap > 0.001:
            create_box(oEditor, "SM_Top_PosY", -hx, sm_gap, z_sm,
                       length, hy - sm_gap, _SM_T, "LPI_SolderMask")
            create_box(oEditor, "SM_Top_NegY", -hx, -hy, z_sm,
                       length, hy - sm_gap, _SM_T, "LPI_SolderMask")
        create_box(oEditor, "SM_Bot", -hx, -hy, z_bot,
                   length, width, _SM_T, "LPI_SolderMask")

    return out


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
            _warn("  zone {}: {}".format(bname, str(e)))
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
        _log("  LengthOp {} -> {} objs, max={:.3f}mm".format(
            op_name, len(obj_list), max_len_mm))
    except Exception as e:
        _warn("  LengthOp {}: {}".format(op_name, str(e)))


def assign_skin_depth_op(oMesh, op_name, obj_list, skin_mm, surf_len_mm):
    try:
        oMesh.AssignSkinDepthOp(
            ["NAME:" + op_name,
             "Enabled:=",           True,
             "Objects:=",           obj_list,
             "RestrictElem:=",      False,
             "NumMaxElem:=",        "1000",
             "SkinDepth:=",         mm(skin_mm),
             "SurfTriMaxLength:=",  mm(surf_len_mm),
             "NumLayers:=",         2]
        )
        _log("  SkinDepthOp {} -> {} objs, depth={:.4f}mm".format(
            op_name, len(obj_list), skin_mm))
    except Exception as e:
        _warn("  SkinDepthOp {}: {}".format(op_name, str(e)))


def assign_mesh(oDesign, pcb, zone_names):
    oMesh = oDesign.GetModule("MeshSetup")
    _log("  Transition zones : {:.3f} mm".format(MESH_TRANS_MM))
    _log("  Trace            : {:.3f} mm".format(MESH_TRACE_MM))
    _log("  Ground planes    : {:.3f} mm".format(MESH_PLANE_MM))
    _log("  Skin depth       : {:.4f} mm".format(MESH_SKIN_MM))

    if zone_names:
        assign_length_op(oMesh, "Mesh_Transition", zone_names, MESH_TRANS_MM)

    assign_length_op(oMesh, "Mesh_Trace", ["Trace_FCu"], MESH_TRACE_MM)

    if pcb["plane_objects"]:
        assign_length_op(oMesh, "Mesh_Planes", pcb["plane_objects"], MESH_PLANE_MM)

    all_cu = pcb["copper_objects"]
    if all_cu:
        assign_skin_depth_op(oMesh, "Mesh_SkinDepth", all_cu,
                             MESH_SKIN_MM, MESH_TRANS_MM)


# ===========================================================================
# SMA connector placement
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
        _warn("  Manual: Insert > 3D Component > {} at ({:.1f},0,{:.4f}) mm".format(
            os.path.basename(comp_path), x_contact, z_pin))
        return

    if side == "right":
        # Mirror about the plane x=hx (normal = +X, base on board right edge).
        # This is geometrically equivalent to reflecting each point (x,y,z) to
        # (2*hx - x, y, z), flipping the cable axis from -X to +X while leaving
        # Y unchanged.  180-deg-Z rotation was used before but ALSO flips Y,
        # producing the wrong Y orientation.
        #
        # record.py (user-recorded AEDT macro) confirms:
        #  - submodel parts follow B2 automatically — they need NOT be selected
        #    individually; the "Submodel part not allowed" warnings from earlier
        #    combined-selection attempts were a red herring.
        #  - oEditor.Mirror on B2 alone is sufficient to reorient the whole
        #    component (outer shell + all inner sub-parts).
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

        # Primary: AEDT auto-assigned body name for the 2nd Insert3DComponent.
        # Submodel parts (INSULATION2, RSHELL2, …) follow B2 automatically.
        mirrored = _try_mirror("2023R1_HRMG_300_468B2", "AEDT-body-B2")

        # Fallback: discover instance names via API (if B-numbering changes).
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

        # Diagnostic: log which submodel parts appeared after insertion.
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
        _log("  Setup HFSS_Adaptive at {:.1f}GHz dS<{:.3f} passes={:d}".format(
            F_ADAPT, MAX_DELTA_S, MAX_PASSES))
        _warn("  TIP: add secondary adaptive point at {:.1f}GHz manually".format(F_ADAPT2) +
              " via Edit Setup > Multi-Frequency Adaptive.")
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
        _log("  Sweep {:.3f}-{:.1f}GHz {:d}pts Interpolating".format(
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

    board_t = sum(t for _, t, _ in stackup)
    hx      = BOARD_LENGTH / 2.0
    design_name = DESIGN_BASE + "_" + str(N_LAYERS) + "L"

    # Locate .a3dcomp relative to this script
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except (NameError, TypeError):
        script_dir = os.getcwd()
    comp_path = os.path.join(script_dir, SMA_COMP_FILE)

    _log("Layers      : {:d}  t={:.4f}mm".format(N_LAYERS, board_t))
    _log("Board       : {:.1f} x {:.1f} mm".format(BOARD_LENGTH, BOARD_WIDTH))
    _log("Trace W     : {:.3f} mm  (50-ohm microstrip)".format(TRACE_WIDTH))
    _log("Solder mask : {}".format(str(SOLDER_MASK)))
    _log("SMA comp    : " + comp_path +
         ("  [OK]" if os.path.exists(comp_path) else "  [MISSING]"))
    _log("Sweep       : {:.3f}-{:.1f}GHz  {:d}pts".format(F_START, F_STOP, F_POINTS))

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

    # Delete existing design with same name, then create fresh
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
    _step("PCB stackup geometry")
    try:
        pcb = build_pcb(oEditor, oProject, stackup,
                        BOARD_LENGTH, BOARD_WIDTH, TRACE_WIDTH, SOLDER_MASK)
    except Exception as e:
        _err("build_pcb: " + str(e))
        _err(traceback.format_exc())
        return

    z_pin = pcb["z_fcu_bot"] + _OZ1 / 2.0
    _log("Board thickness : {:.4f} mm".format(pcb["board_thickness"]))
    _log("F.Cu range      : [{:.4f}, {:.4f}] mm".format(
        pcb["z_fcu_bot"], pcb["z_fcu_top"]))
    _log("SMA pin Z (mid) : {:.4f} mm".format(z_pin))

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
    _log("DONE  {:.1f} s".format(elapsed))
    _log("Next steps:")
    _log("  1. Verify port integration lines (outer -> pin)")
    _log("  2. Add secondary adaptive point at {:.1f}GHz in Edit Setup".format(F_ADAPT2))
    _log("  3. Analyze > HFSS_Adaptive")
    _log("=" * 55)


try:
    main()
except Exception as _ex:
    _err("FATAL: " + str(_ex))
    _err(traceback.format_exc())
