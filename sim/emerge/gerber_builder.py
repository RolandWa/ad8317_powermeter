"""
gerber_builder.py — Gerber-to-geometry library for EMerge FEM pipelines.

Extracted from emerge_runner.py so that both the KiCad plugin pipeline
and standalone debug scripts (test_gerber_import.py) share the same
well-tested geometry building, meshing, and viewing logic.

Public API
----------
load_copper_layers(pcb, stackup, pcb_path, gerber_dir, ...)
    Load F.Cu / B.Cu (and any additional copper layers) from .gbr files
    into a FileBasedPCB using layer_from_file().

build_and_commit(sim, pcb, board_t, xmin, ymin, xmax, ymax, ...)
    generate_pcb() + generate_air() + open_pml_region() + commit_geometry().
    Returns (pcb_vol, air_vol).

fix_sliver_faces(gmsh_module, ...)
    After commit_geometry(), set fine mesh sizes at OCC boolean artefact
    surfaces smaller than threshold_m × threshold_m.

gmsh_view_geometry(title)
    Open GMSH FLTK viewer showing filled shaded surfaces (no mesh).

gmsh_view_mesh(title)
    Open GMSH FLTK viewer showing the surface mesh.

sim_view(sim, ...)
    Call sim.view() probing the available keyword arguments.

save_geometry_debug(sim, output_dir, ...)
    Write off-screen PNG + STEP/BREP snapshot for debug inspection.
"""

import inspect
import pathlib
import re as _re
import time
import traceback

# =============================================================================
# Gerber bounding-box crop
# =============================================================================

def crop_gerber_to_bbox(src: pathlib.Path, dst: pathlib.Path,
                        xmin_m: float, ymin_m: float,
                        xmax_m: float, ymax_m: float,
                        log=None) -> bool:
    """
    Write a cropped copy of a Gerber (.gbr) keeping only copper features
    whose centroid falls inside the simulation bounding box.

    sim bounds are in metres, Gerber Y-up convention.
    KiCad Gerbers use millimetres with Y-down, so the box is converted:
      gx = x_m * 1000
      gy = -y_m * 1000   (Y-up → Y-down flip; ymax/ymin swap)

    Kept elements:
      D03 flash  — centroid of the flash position
      D01 draw   — current pen position (midpoint of move)
      Region blocks — all interior coordinates kept once the region opens
                      inside the box (avoids broken polygon outlines)

    Header / aperture / format lines are always preserved so the output is
    a valid Gerber file.  Returns True if dst was written, False on error.
    """
    def _log(msg):
        if log:
            log(msg)

    gx_min = xmin_m * 1e3;  gx_max = xmax_m * 1e3
    gy_min = -ymax_m * 1e3; gy_max = -ymin_m * 1e3   # Y-flip: ymax/ymin swap

    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _log(f"  crop_gerber: cannot read {src.name}: {exc}")
        return False

    fmt_m = _re.search(r'%FS[LT]A[XY](\d)(\d)[XY]\d\d', text)
    if fmt_m:
        frac_d = int(fmt_m.group(2))
    else:
        frac_d = 6   # KiCad default FSLAX46Y46

    scale = 10.0 ** frac_d

    def _to_mm(raw: str) -> float:
        return int(raw) / scale

    coord_re     = _re.compile(r'(?:X(-?\d+))?(?:Y(-?\d+))?D0*([123])\*')
    region_start = _re.compile(r'G36\*')
    region_end   = _re.compile(r'G37\*')

    def _in_box(x_mm: float, y_mm: float) -> bool:
        return gx_min <= x_mm <= gx_max and gy_min <= y_mm <= gy_max

    cur_x = 0.0
    cur_y = 0.0
    in_region      = False
    region_in_box  = False
    in_header      = True

    header_lines: list[str] = []
    body_lines:   list[str] = []

    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()

        if in_header:
            header_lines.append(raw_line)
            if coord_re.search(line) or line == 'M02*':
                in_header = False
            continue

        if region_start.match(line):
            in_region     = True
            region_in_box = False
            body_lines.append(raw_line)
            continue

        if region_end.match(line):
            in_region = False
            body_lines.append(raw_line)
            continue

        m = coord_re.search(line)
        if m:
            raw_x, raw_y, d = m.group(1), m.group(2), m.group(3)
            if raw_x is not None:
                cur_x = _to_mm(raw_x)
            if raw_y is not None:
                cur_y = _to_mm(raw_y)

            if in_region:
                # Keep region if any vertex falls in the box
                if _in_box(cur_x, cur_y):
                    region_in_box = True
                body_lines.append(raw_line)
                continue

            if d == '3':   # D03 flash
                if _in_box(cur_x, cur_y):
                    body_lines.append(raw_line)
                continue

            if d == '1':   # D01 draw — keep if endpoint is in box
                if _in_box(cur_x, cur_y):
                    body_lines.append(raw_line)
                continue

            # D02 move — always keep
            body_lines.append(raw_line)
            continue

        body_lines.append(raw_line)

    kept = sum(1 for l in body_lines if coord_re.search(l.strip()))
    _log(f"  Gerber crop {src.name}: {kept} features in "
         f"bbox ({gx_min:.1f},{gy_min:.1f})–({gx_max:.1f},{gy_max:.1f}) mm")

    try:
        dst.write_text("".join(header_lines + body_lines), encoding="utf-8")
        return True
    except Exception as exc:
        _log(f"  crop_gerber: cannot write {dst}: {exc}")
        return False


# ── lazy gmsh import (available after emerge initialises GMSH) ────────────────

def _gmsh():
    import gmsh as _g
    return _g


# =============================================================================
# Copper layer loader
# =============================================================================

def load_copper_layers(pcb, stackup: dict, pcb_path: pathlib.Path,
                       gerber_dir: pathlib.Path,
                       circ_segs: int = 64, res_mm: float = 0.05,
                       sim_bounds: tuple | None = None,
                       log=None) -> int:
    """
    Load copper Gerber files into *pcb* via layer_from_file().

    When *sim_bounds* = (xmin, ymin, xmax, ymax) in metres (Gerber Y-up) is
    provided, each Gerber is pre-cropped to that bounding box before being
    passed to EMerge.  This restricts copper geometry to the simulation area
    of interest and avoids parser failures from complex fills elsewhere.

    Args:
        pcb         : FileBasedPCB instance (created before calling this).
        stackup     : dict from kicad_reader.read_stackup().
        pcb_path    : path to .kicad_pcb — used to derive Gerber file stem.
        gerber_dir  : directory containing the exported .gbr files.
        circ_segs   : arc segments per circle (default 64).
        res_mm      : geometry resolution in mm (default 0.05).
        sim_bounds  : (xmin, ymin, xmax, ymax) metres, Gerber Y-up.
                      Gerbers are pre-cropped to this box when given.
        log         : callable(str) for progress messages, or None.

    Returns:
        Number of layers successfully loaded.
    """
    def _log(msg):
        if log:
            log(msg)

    cu_layers = [l for l in stackup.get("layers", []) if l["type"] == "copper"]
    suffix = ", pre-crop=ON)" if sim_bounds else ")"
    _log(f"Loading {len(cu_layers)} copper layer(s) "
         f"(n_circ={circ_segs}, res_mm={res_mm}{suffix}")

    pcb_stem = pcb_path.stem if (pcb_path and pcb_path.exists()) else ""
    loaded   = 0

    crop_dir = gerber_dir / "_cropped"
    if sim_bounds:
        crop_dir.mkdir(parents=True, exist_ok=True)

    for idx, layer in enumerate(cu_layers):
        name     = layer["name"]
        gbr_stem = name.replace(".", "_")
        candidates = [
            gerber_dir / f"{pcb_stem}-{gbr_stem}.gbr",
            gerber_dir / f"{gbr_stem}.gbr",
        ]
        gbr = next((p for p in candidates if p.exists()), None)

        if gbr is None:
            _log(f"  [{idx}] {name}: WARNING — Gerber not found "
                 f"(searched: {[c.name for c in candidates]})")
            continue

        if sim_bounds:
            xmin_m, ymin_m, xmax_m, ymax_m = sim_bounds
            cropped = crop_dir / gbr.name
            ok = crop_gerber_to_bbox(gbr, cropped,
                                     xmin_m, ymin_m, xmax_m, ymax_m,
                                     log=_log)
            load_path = cropped if ok else gbr
        else:
            load_path = gbr

        size_kb = load_path.stat().st_size / 1024
        _log(f"  [{idx}] {name}: {load_path.name}  ({size_kb:.0f} kB)  parsing ...")
        t0 = time.monotonic()
        try:
            pcb.layer_from_file(idx, str(load_path),
                                res_mm=res_mm,
                                n_circ_segments=circ_segs)
            _log(f"  [{idx}] {name}: done  ({time.monotonic()-t0:.1f} s)")
            loaded += 1
        except Exception as exc:
            _log(f"  [{idx}] {name}: FAILED — {exc}")
            if log:
                traceback.print_exc()

    return loaded


# =============================================================================
# PCB geometry builder + commit
# =============================================================================

def build_and_commit(sim, pcb, board_t: float,
                     xmin: float, ymin: float,
                     xmax: float, ymax: float,
                     port_geos: tuple = (),
                     pml_scale_xy: float = 0.15,
                     pml_scale_z:  float = 0.5,
                     log=None):
    """
    Build 3-D PCB solid, add air box + PML, and commit to *sim*.

    Args:
        sim           : EMerge Simulation object.
        pcb           : FileBasedPCB / PCBNew instance with layers loaded.
        board_t       : Board thickness in metres.
        xmin,ymin,xmax,ymax : Simulation domain bounds in metres.
        port_geos     : Iterable of lumped-port geometry objects to commit.
        pml_scale_xy  : PML thickness = min(board_W, board_H) * scale.
        pml_scale_z   : PML z thickness = air_height * scale.
        log           : callable(str) for progress messages, or None.

    Returns:
        (pcb_vol, air_vol) — volume objects returned by EMerge.
    """
    from emerge._emerge.geo.open_region import open_pml_region

    def _log(msg):
        if log:
            log(msg)

    board_w   = xmax - xmin
    board_h   = ymax - ymin
    air_height = board_t * 4
    pml_xy    = max(0.005, min(board_w, board_h) * pml_scale_xy)
    pml_z     = max(0.005, air_height * pml_scale_z)

    _log(f"  PML: xy={pml_xy*1e3:.1f} mm  z={pml_z*1e3:.1f} mm  "
         f"air_h={air_height*1e3:.1f} mm")

    _log("  generate_pcb(split_z=True, merge=True) ...")
    t0 = time.monotonic()
    try:
        pcb_vol = pcb.generate_pcb(split_z=True, merge=True)
    except TypeError:
        pcb_vol = pcb.generate_pcb()
    _log(f"    done  ({time.monotonic()-t0:.1f} s)")

    _log("  generate_air() ...")
    t1 = time.monotonic()
    air_vol = pcb.generate_air(height=air_height)
    _log(f"    done  ({time.monotonic()-t1:.1f} s)")

    pml = open_pml_region(pml_xy, pml_xy, pml_z)

    _log("  commit_geometry() — fusing CAD solids ...")
    t2 = time.monotonic()
    sim.commit_geometry(pcb_vol, air_vol, pml, *port_geos)
    _log(f"    done  ({time.monotonic()-t2:.1f} s)")

    return pcb_vol, air_vol


# =============================================================================
# Sliver face compound (OCC boolean artefacts — mesh-level fix)
# =============================================================================

def compound_sliver_surfaces(gmsh_module=None, threshold_m: float = 0.1e-3,
                             log=None) -> int:
    """
    Group OCC boolean-artefact sliver surfaces with their largest adjacent
    non-sliver surface and call gmsh.model.mesh.setCompound(2, ...) once per
    group.

    Each anchor (non-sliver) surface appears in exactly ONE setCompound call
    that collects ALL slivers that chose it as their best neighbor.  This
    avoids repeated setCompound calls on the same anchor that silently
    overwrite each other and leave some slivers un-compounded.

    Compounding a sliver with a larger surface tells GMSH to parametrize and
    mesh them together — avoiding independent 2D Delaunay edge recovery on
    tiny sliver boundaries, which is the root cause of "Unable to recover
    the edge" failures.

    Must be called after commit_geometry() and occ.synchronize(), but before
    generate(3).  Does NOT modify OCC geometry or tags.

    Returns: number of sliver surfaces successfully placed in a compound.
    """
    def _log(msg):
        if log:
            log(msg)

    if gmsh_module is None:
        import gmsh as gmsh_module

    g = gmsh_module

    all_surf_tags = [tag for _, tag in g.model.getEntities(2)]

    # Identify slivers
    sliver_set: set[int] = set()
    for tag in all_surf_tags:
        x0, y0, z0, x1, y1, z1 = g.model.getBoundingBox(2, tag)
        if (x1 - x0) < threshold_m and (y1 - y0) < threshold_m:
            sliver_set.add(tag)

    if not sliver_set:
        _log(f"  Compound sliver surfaces: 0  (no slivers < {threshold_m*1e3:.2f} mm)")
        return 0

    # Build curve → surface adjacency map
    curve_to_surfs: dict[int, list[int]] = {}
    for tag in all_surf_tags:
        for _, ctag in g.model.getBoundary([(2, tag)], oriented=False):
            curve_to_surfs.setdefault(abs(ctag), []).append(tag)

    def _bbox_area(stag: int) -> float:
        x0, y0, _, x1, y1, _ = g.model.getBoundingBox(2, stag)
        return (x1 - x0) * (y1 - y0)

    def _get_adj(stag: int) -> set[int]:
        adj: set[int] = set()
        for _, ctag in g.model.getBoundary([(2, stag)], oriented=False):
            for s in curve_to_surfs.get(abs(ctag), []):
                if s != stag:
                    adj.add(s)
        return adj

    # For each sliver, find its best non-sliver anchor (largest adjacent non-sliver)
    # anchor → set of slivers that chose it
    anchor_to_slivers: dict[int, list[int]] = {}
    n_no_anchor = 0
    try:
        for sliver in sliver_set:
            adj       = _get_adj(sliver)
            non_sliver_adj = [s for s in adj if s not in sliver_set]
            if non_sliver_adj:
                best = max(non_sliver_adj, key=_bbox_area)
            elif adj:
                # all adjacent surfaces are also slivers — pick the largest one
                best = max(adj, key=_bbox_area)
            else:
                n_no_anchor += 1
                continue
            anchor_to_slivers.setdefault(best, []).append(sliver)
    except Exception as exc:
        _log(f"  Compound sliver surfaces error (grouping): {exc}")
        return 0

    # One setCompound call per anchor (avoids overwriting earlier compounds)
    n_compounded = 0
    try:
        for anchor, slivers in anchor_to_slivers.items():
            g.model.mesh.setCompound(2, [anchor] + slivers)
            n_compounded += len(slivers)
        _log(f"  Compound sliver surfaces: {n_compounded} in {len(anchor_to_slivers)} "
             f"groups  (threshold {threshold_m*1e3:.2f} mm)"
             + (f"  [{n_no_anchor} isolated slivers skipped]" if n_no_anchor else ""))
    except Exception as exc:
        _log(f"  Compound sliver surfaces error (setCompound): {exc}")

    return n_compounded


# =============================================================================
# Sliver face fix (OCC boolean artefacts)
# =============================================================================

def fix_sliver_faces(gmsh_module=None, threshold_m: float = 0.1e-3,
                     sim=None, log=None) -> int:
    """
    After commit_geometry(), OCC boolean operations leave tiny residual
    surfaces (~10 µm wide).  GMSH's Delaunay edge recovery fails on these.

    Two complementary fixes are applied:
    1. setSize() on boundary vertices so GMSH inherits tiny sizes from points.
    2. sim.mesher._set_size_on_face() adds a background-field constraint that
       survives sim.generate_mesh()'s _configure_mesh_size() call (which wipes
       boundary-inheritance by calling setSizeFromBoundary=0 on all faces).

    When calling through sim.generate_mesh(), pass the Simulation object as
    `sim` so that fix (2) is applied — fix (1) alone is insufficient because
    _configure_mesh_size() overrides point-level sizes via its background Min
    field, asking GMSH to create ~0.3 mm triangles on an 11 µm surface.

    Args:
        gmsh_module  : the gmsh module (import gmsh); None → auto-import.
        threshold_m  : surfaces with both width AND height below this value
                       are treated as slivers.  Default: 0.1 mm.
        sim          : EMerge Simulation object — if provided, registers each
                       sliver surface with sim.mesher so the background Min
                       field uses the tiny target size.
        log          : callable(str) for progress messages, or None.

    Returns:
        Number of sliver surfaces patched.
    """
    def _log(msg):
        if log:
            log(msg)

    if gmsh_module is None:
        import gmsh as gmsh_module

    g       = gmsh_module
    slivers = 0

    try:
        for _, stag in g.model.getEntities(2):
            x0, y0, z0, x1, y1, z1 = g.model.getBoundingBox(2, stag)
            sw = x1 - x0
            sh = y1 - y0
            if sw < threshold_m and sh < threshold_m:
                size = max(min(sw, sh) * 0.5, 1e-6)
                # Fix 1: set size at boundary vertices (works without sim.generate_mesh)
                bcs = g.model.getBoundary([(2, stag)], oriented=False)
                pts = g.model.getBoundary(bcs, oriented=False, combined=True)
                if pts:
                    g.model.mesh.setSize(pts, size)
                # Fix 2: register boundary curves and surface in the mesher's
                # background Min field so the tiny size survives EMerge's
                # _configure_mesh_size() which disables boundary inheritance.
                # Curves must be constrained too — Constant field VIn applies
                # inside surfaces, not on their boundary curves.
                if sim is not None:
                    try:
                        curve_tags = [abs(c[1]) for c in bcs]
                        if curve_tags:
                            sim.mesher._set_size_on_edge(curve_tags, size)
                        sim.mesher._set_size_on_face([stag], size)
                    except Exception:
                        pass
                slivers += 1
        _log(f"  Sliver surfaces patched: {slivers}  (threshold {threshold_m*1e3:.2f} mm)")
    except Exception as exc:
        _log(f"  Sliver fix error: {exc}")

    return slivers


# =============================================================================
# GMSH FLTK viewers
# =============================================================================

def gmsh_view_geometry(title: str = "Geometry — close window to continue"):
    """
    Open the GMSH FLTK interactive viewer showing filled shaded surfaces.
    Blocks until the window is closed.
    """
    g = _gmsh()
    if not g.isInitialized():
        return
    try:
        g.option.setNumber("General.Verbosity",    0)
        g.option.setNumber("Geometry.Surfaces",    1)   # filled surfaces
        g.option.setNumber("Geometry.SurfaceType", 2)   # shaded + edges
        g.option.setNumber("Geometry.Points",      0)
        g.option.setNumber("Geometry.Lines",       1)
        g.option.setNumber("General.RotationX",  -65)
        g.option.setNumber("General.RotationY",    0)
        g.option.setNumber("General.RotationZ",   25)
        print(f"\n{'='*60}\n{title}\n{'='*60}")
        g.fltk.initialize()
        g.fltk.run()
        g.fltk.finalize()
        print("Geometry viewer closed.")
    except Exception as exc:
        print(f"GMSH geometry viewer failed: {exc}")


def gmsh_view_mesh(title: str = "Mesh — close window to continue"):
    """
    Open the GMSH FLTK interactive viewer showing the surface mesh.
    Blocks until the window is closed.
    """
    g = _gmsh()
    if not g.isInitialized():
        return
    try:
        g.option.setNumber("General.Verbosity",   0)
        g.option.setNumber("Mesh.SurfaceEdges",   1)
        g.option.setNumber("Mesh.SurfaceFaces",   1)
        g.option.setNumber("Mesh.VolumeEdges",    0)
        g.option.setNumber("Mesh.VolumeFaces",    0)
        g.option.setNumber("General.RotationX", -65)
        g.option.setNumber("General.RotationY",   0)
        g.option.setNumber("General.RotationZ",  25)
        print(f"\n{'='*60}\n{title}\n{'='*60}")
        g.fltk.initialize()
        g.fltk.run()
        g.fltk.finalize()
        print("Mesh viewer closed.")
    except Exception as exc:
        print(f"GMSH mesh viewer failed: {exc}")


# =============================================================================
# sim.view() wrapper
# =============================================================================

def sim_view(sim, plot_mesh: bool = False, labels: bool = True,
             bc: bool = True, off_screen: bool = False,
             screenshot=None):
    """
    Call sim.view() with only the keyword arguments that the installed
    EMerge version accepts.  Different v2.8.x builds drop or rename params
    (e.g. 'bc', 'labels', 'off_screen', 'screenshot'), so we probe the
    signature rather than hard-coding the call.
    """
    try:
        sig_params = set(inspect.signature(sim.view).parameters.keys())
    except (TypeError, ValueError):
        sig_params = set()

    kwargs = {}
    if not sig_params or "plot_mesh" in sig_params: kwargs["plot_mesh"] = plot_mesh
    if "labels"     in sig_params:                  kwargs["labels"]    = labels
    if "bc"         in sig_params:                  kwargs["bc"]        = bc
    if "off_screen" in sig_params and off_screen:   kwargs["off_screen"] = off_screen
    if "screenshot" in sig_params and screenshot:   kwargs["screenshot"] = screenshot
    sim.view(**kwargs)


# =============================================================================
# Geometry debug snapshot
# =============================================================================

def save_geometry_debug(sim, output_dir, report_lines=None, verbose: bool = True):
    """
    Save geometry snapshot files for debug inspection.

    Attempts (in order):
      1. Off-screen PNG via sim.view(off_screen=True, screenshot=...)
      2. STEP export via gmsh.write()
      3. BREP export as fallback

    All files written to output_dir/geometry_debug.*

    Args:
        sim          : EMerge Simulation object (geometry already committed).
        output_dir   : pathlib.Path — destination directory.
        report_lines : list to append log messages to (or None).
        verbose      : also print log messages to stdout.
    """
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []

    def _log(msg):
        if report_lines is not None:
            report_lines.append(msg)
        if verbose:
            print(msg, flush=True)

    # 1. Off-screen PNG
    png_path = out / "geometry_debug.png"
    try:
        sim_view(sim, plot_mesh=False, labels=True, bc=True,
                 off_screen=True, screenshot=str(png_path))
        if png_path.exists():
            saved.append(("PNG", png_path))
        else:
            _log("  Geometry PNG: sim.view() ran but no file written "
                 "(off_screen/screenshot not supported by this EMerge build)")
    except Exception as exc:
        _log(f"  Geometry PNG failed: {exc}")

    # 2. GMSH geometry export
    import gmsh as _g
    for ext, label in [(".step", "STEP"), (".brep", "BREP"), (".geo_unrolled", "GEO")]:
        geo_path = out / f"geometry_debug{ext}"
        try:
            _g.write(str(geo_path))
            saved.append((label, geo_path))
            break
        except Exception as exc:
            _log(f"  {label} export failed: {exc}")

    if saved:
        _log(f"  Geometry snapshot ({len(saved)} file(s)):")
        for label, path in saved:
            _log(f"    {label}: {path}")
    else:
        _log("  Geometry snapshot: all formats failed — "
             "inspect geometry via the interactive viewer")
