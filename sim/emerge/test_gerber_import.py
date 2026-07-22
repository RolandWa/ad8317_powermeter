"""
test_gerber_import.py — standalone Gerber import + 3D view for EMerge debugging.

Uses gerber_builder.py for geometry building, meshing, and viewing so that
the same logic runs in both the debug script and the full KiCad pipeline.

Usage:
    python test_gerber_import.py                          # full domain with air box
    python test_gerber_import.py --pcb-only               # PCB board only, no air box
    python test_gerber_import.py --gerbers path/to/gerbers
"""

import pathlib
import sys
import time
import traceback

# ── paths ─────────────────────────────────────────────────────────────────────
_HERE           = pathlib.Path(__file__).parent
_PCB_DEFAULT    = _HERE.parent.parent / "kicad" / "ad8317_powermeter.kicad_pcb"
_GERBER_DEFAULT = _HERE / "emerge_output" / "gerbers"

# ── mesh tunables ─────────────────────────────────────────────────────────────
N_CIRC_SEGMENTS = 64      # arc segments per pad circle
RES_MM          = 0.05    # Gerber geometry resolution [mm]
CBR             = 40      # curved_boundary_resolution passed to GMSH

# ─────────────────────────────────────────────────────────────────────────────

def _log(msg):
    print(msg, flush=True)


def _import_emerge():
    try:
        from emerge.beta.gerber import FileBasedPCB
        from emerge._emerge.geo.pcb import PCBLayer
        from emerge import Simulation, Material
        return FileBasedPCB, PCBLayer, Simulation, Material
    except ImportError as exc:
        _log(f"ERROR: cannot import emerge: {exc}")
        traceback.print_exc()
        sys.exit(1)


def _read_stackup(pcb_path):
    defaults = {
        "board_thickness_mm": 1.6,
        "copper_thickness_mm": 0.035,
        "er": 4.4, "tand": 0.02,
        "copper_layers": 2,
        "layers": [
            {"name": "F.Cu", "type": "copper",     "thick": 0.035, "er": None, "tand": None},
            {"name": "core", "type": "dielectric",  "thick": 1.530, "er": 4.4,  "tand": 0.02},
            {"name": "B.Cu", "type": "copper",     "thick": 0.035, "er": None, "tand": None},
        ],
    }
    if pcb_path is None or not pcb_path.exists():
        _log("  PCB not found — using hardcoded FR4 stackup defaults")
        return defaults
    try:
        sys.path.insert(0, str(_HERE))
        from kicad_reader import read_stackup
        s = read_stackup(pcb_path)
        _log(f"  Stackup from PCB: {s['copper_layers']} layers, "
             f"h={s['board_thickness_mm']} mm, er={s['er']}, tand={s['tand']}")
        return s
    except Exception as exc:
        _log(f"  kicad_reader failed ({exc}) — using defaults")
        return defaults


def _read_outline(pcb_path):
    if pcb_path is None or not pcb_path.exists():
        return None
    try:
        sys.path.insert(0, str(_HERE))
        from kicad_reader import read_board_outline
        pts = read_board_outline(pcb_path)
        if pts:
            _log(f"  Board outline: {len(pts)} vertices")
            return pts
    except Exception as exc:
        _log(f"  read_board_outline failed: {exc}")
    return None


def main(gerber_dir: pathlib.Path, pcb_path: pathlib.Path, pcb_only: bool = False):
    _log("=" * 60)
    _log("EMerge Gerber import test")
    _log(f"  Gerbers  : {gerber_dir}")
    _log(f"  PCB      : {pcb_path}")
    _log(f"  pcb_only : {pcb_only}")
    _log(f"  n_circ   : {N_CIRC_SEGMENTS}  res_mm={RES_MM}  cbr={CBR}")
    _log("=" * 60)

    if not gerber_dir.is_dir():
        _log(f"ERROR: Gerber directory not found: {gerber_dir}")
        sys.exit(1)

    gbr_files = sorted(gerber_dir.glob("*.gbr"))
    _log(f"\nFound {len(gbr_files)} .gbr files:")
    for f in gbr_files:
        _log(f"  {f.name:<55} {f.stat().st_size/1024:.1f} kB")
    if not gbr_files:
        _log("ERROR: No .gbr files — export Gerbers from KiCad first.")
        sys.exit(1)

    # ── library modules ───────────────────────────────────────────────────────
    try:
        from gerber_builder import (
            load_copper_layers, build_and_commit, fix_sliver_faces,
            gmsh_view_geometry, gmsh_view_mesh,
        )
    except ImportError as exc:
        _log(f"ERROR: gerber_builder not found: {exc}")
        sys.exit(1)

    FileBasedPCB, PCBLayer, Simulation, Material = _import_emerge()

    # ── stackup ───────────────────────────────────────────────────────────────
    stackup  = _read_stackup(pcb_path)
    board_t  = stackup["board_thickness_mm"] * 1e-3
    er       = stackup["er"]
    tand     = stackup["tand"]

    cu_mat  = Material(cond=5.96e7,    name="Copper")
    fr4_mat = Material(er=er, tand=tand, name="FR4")

    stack_layers = []
    for lyr in stackup["layers"]:
        t = lyr["thick"] * 1e-3
        if t == 0:
            continue
        if lyr["type"] == "copper":
            stack_layers.append(PCBLayer(thickness=t, material=cu_mat,  name=lyr["name"]))
        else:
            stack_layers.append(PCBLayer(thickness=t, material=fr4_mat, name=lyr["name"]))

    # ── board outline → domain bounds (Gerber Y is negated vs KiCad) ─────────
    outline_pts = _read_outline(pcb_path)
    margin_m    = max(0.005, board_t * 3)

    if outline_pts:
        xs = [ p[0] * 1e-3 for p in outline_pts]
        ys = [-p[1] * 1e-3 for p in outline_pts]   # Gerber negates KiCad Y-down
        xmin = min(xs) - margin_m;  xmax = max(xs) + margin_m
        ymin = min(ys) - margin_m;  ymax = max(ys) + margin_m
        _log(f"  Domain: ({xmin*1e3:.1f}, {ymin*1e3:.1f}) – "
             f"({xmax*1e3:.1f}, {ymax*1e3:.1f}) mm  "
             f"[board {(max(xs)-min(xs))*1e3:.1f} x {(max(ys)-min(ys))*1e3:.1f} mm "
             f"+ {margin_m*1e3:.1f} mm margin]")
    else:
        xmin, xmax = -0.025, 0.025
        ymin, ymax = -0.025, 0.025
        _log("  Board outline not found — using 50×50 mm fallback domain")

    # ── Simulation FIRST — initialises GMSH geometry manager ─────────────────
    _log("\nCreating Simulation ...")
    sim = Simulation("gerber_import_test", loglevel="WARNING")
    sim.set_physics(microwave=True, heatconduction=False)
    _log("  done")

    # ── FileBasedPCB ──────────────────────────────────────────────────────────
    _log("\nCreating FileBasedPCB ...")
    t0  = time.monotonic()
    pcb = FileBasedPCB(
        thickness      = board_t,
        unit           = 1.0,
        stack          = stack_layers or None,
        layers         = stackup["copper_layers"],
        trace_material = cu_mat,
    )
    _log(f"  done ({time.monotonic()-t0:.1f} s)")
    pcb.set_bounds(xmin, ymin, xmax, ymax)

    # ── Load copper layers via gerber_builder ─────────────────────────────────
    _log("")
    load_copper_layers(
        pcb        = pcb,
        stackup    = stackup,
        pcb_path   = pcb_path,
        gerber_dir = gerber_dir,
        circ_segs  = N_CIRC_SEGMENTS,
        res_mm     = RES_MM,
        log        = _log,
    )

    # ── generate PCB 3D solid ─────────────────────────────────────────────────
    _log("\nBuilding 3D PCB geometry ...")
    t1 = time.monotonic()
    try:
        pcb_vol = pcb.generate_pcb(split_z=True, merge=True)
    except TypeError:
        pcb_vol = pcb.generate_pcb()
    _log(f"  generate_pcb done ({time.monotonic()-t1:.1f} s)")

    # ── Step 1: geometry view (before air box so PCB layers are visible) ──────
    gmsh_view_geometry("Step 1 — PCB geometry (close window to continue to mesh)")

    # ── commit (PCB-only or full domain with air box) ─────────────────────────
    if pcb_only:
        _log("\nPCB-only mode — committing without air box ...")
        t2 = time.monotonic()
        sim.commit_geometry(pcb_vol)
        _log(f"  commit_geometry done ({time.monotonic()-t2:.1f} s)")
    else:
        _log("\nAdding air box + PML ...")
        build_and_commit(
            sim     = sim,
            pcb     = pcb,
            board_t = board_t,
            xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
            log     = _log,
        )

    # Curved boundary resolution (after commit)
    try:
        sim.mesher.set_curved_boundary_meshing(CBR)
        _log(f"  curved_boundary_resolution = {CBR}")
    except Exception:
        pass

    # ── Sliver face fix ───────────────────────────────────────────────────────
    fix_sliver_faces(log=_log)

    # ── Step 2: generate mesh + show ──────────────────────────────────────────
    _log("\nGenerating mesh (MeshAdapt — more robust on PCBs) ...")
    import gmsh as _gmsh
    t_mesh = time.monotonic()
    for opt, val in [("Mesh.Algorithm",  1),   # MeshAdapt: no Delaunay edge recovery
                     ("Mesh.Algorithm3D",1),
                     ("Mesh.Smoothing", 10)]:
        try:    _gmsh.option.setNumber(opt, val)
        except Exception: pass
    try:
        _gmsh.model.mesh.generate(3)
        _log(f"  Mesh done ({time.monotonic()-t_mesh:.1f} s)")
    except Exception as exc:
        _log(f"  Mesh finished with errors ({time.monotonic()-t_mesh:.1f} s): {exc}")
        _log("  Partial mesh may still be viewable.")

    gmsh_view_mesh("Step 2 — Mesh view (close window to exit)")
    _log("Done.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Test EMerge Gerber import + 3D view")
    p.add_argument("--gerbers",  default=str(_GERBER_DEFAULT),
                   help=f"Gerber directory (default: {_GERBER_DEFAULT})")
    p.add_argument("--pcb",      default=str(_PCB_DEFAULT),
                   help=f"KiCad PCB file (default: {_PCB_DEFAULT})")
    p.add_argument("--pcb-only", action="store_true",
                   help="Mesh PCB board only — skip air box and PML")
    args = p.parse_args()

    main(
        gerber_dir = pathlib.Path(args.gerbers),
        pcb_path   = pathlib.Path(args.pcb),
        pcb_only   = args.pcb_only,
    )
