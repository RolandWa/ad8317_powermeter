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

    sim bounds are in metres, same Y convention as KiCad Gerber files (Y-up, negative Y).
    KiCad Gerbers also use Y-up (negative Y), so no sign flip is needed:
      gx = x_m * 1000
      gy = y_m * 1000   (no flip)

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

    # sim_bounds are in metres, same Y convention as KiCad Gerber files (Y-up, negative Y).
    # No sign flip needed — just scale to mm.
    gx_min = xmin_m * 1e3;  gx_max = xmax_m * 1e3
    gy_min = ymin_m * 1e3;  gy_max = ymax_m * 1e3

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
    in_region       = False
    region_in_box   = False
    region_buf: list[str] = []   # buffered region lines, flushed only if in-box
    in_header       = True
    prev_in_box     = False      # whether the pen was inside the box before last move

    header_lines: list[str] = []
    body_lines:   list[str] = []

    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()

        if in_header:
            if coord_re.search(line) or line == 'M02*':
                in_header = False
                # Fall through — process this line as the first body command.
            else:
                header_lines.append(raw_line)
                continue

        if region_start.match(line):
            in_region     = True
            region_in_box = False
            region_buf    = [raw_line]
            continue

        if region_end.match(line):
            in_region = False
            if region_in_box:
                # Only flush the whole region if at least one vertex was inside
                region_buf.append(raw_line)
                body_lines.extend(region_buf)
            region_buf = []
            continue

        m = coord_re.search(line)
        if m:
            raw_x, raw_y, d = m.group(1), m.group(2), m.group(3)
            prev_x, prev_y  = cur_x, cur_y
            if raw_x is not None:
                cur_x = _to_mm(raw_x)
            if raw_y is not None:
                cur_y = _to_mm(raw_y)

            if in_region:
                if _in_box(cur_x, cur_y):
                    region_in_box = True
                region_buf.append(raw_line)
                continue

            if d == '3':   # D03 flash — keep if centroid in box
                if _in_box(cur_x, cur_y):
                    body_lines.append(raw_line)
                continue

            if d == '1':   # D01 draw — keep if either endpoint in box
                if _in_box(cur_x, cur_y) or _in_box(prev_x, prev_y):
                    body_lines.append(raw_line)
                continue

            # D02 move — only keep if destination is in box (sets pen for D01)
            if _in_box(cur_x, cur_y):
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
    isolated_slivers: list[int] = []
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
                # No adjacent surfaces at all — isolated ghost face.
                # Cannot be compounded; must be handled by remove_ghost_faces().
                isolated_slivers.append(sliver)
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
             f"groups  (threshold {threshold_m*1e3:.2f} mm)")
        if isolated_slivers:
            _log(f"  WARNING: {len(isolated_slivers)} isolated sliver(s) with no adjacent "
                 f"surface — cannot compound; should have been removed by "
                 f"remove_ghost_faces(): {isolated_slivers[:10]}"
                 + (f"  ... +{len(isolated_slivers)-10} more" if len(isolated_slivers) > 10 else ""))
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
# Ghost face removal (OCC boolean artefacts with no parent volume)
# =============================================================================

def remove_ghost_faces(gmsh_module=None, threshold_m: float = 0.1e-3,
                       log=None) -> int:
    """
    Neutralise dangling 2D surfaces that have no adjacent 3D volume and zero
    Z-thickness.  These are OCC boolean artefacts — typically coplanar copper
    fragments from the Gerber import that were not absorbed into any solid
    during commit_geometry().

    TetGen treats every surface in the GMSH model as a PLC constraint and
    tries to recover all its boundary edges.  A ghost face with degenerate
    Z=0 geometry makes that impossible, causing repeated
    "Unable to recover the edge on surface N" failures that no algorithm
    switch can cure.

    Strategy: force a trivial transfinite mesh on each ghost face (2 nodes per
    boundary curve, setTransfiniteSurface).  This gives TetGen clean, trivially
    recoverable edges on a nearly-zero-area face without modifying the model
    topology.  We deliberately do NOT call removeEntities() — that leaves OCC's
    internal BVH/topology caches in an inconsistent state which causes a native
    SEGFAULT on the next GMSH API call from any background OCC thread.

    Detection criteria (ALL must be true):
      • Surface has no adjacent volumes (getAdjacencies returns empty list)
      • Z span of the bounding box < 1 µm  (coplanar — lies on a Cu layer)
      • XY bbox diagonal < threshold_m  (small — classifies it as a sliver)

    Must be called after commit_geometry() / occ.synchronize(), before
    sim.generate_mesh().

    Returns: number of ghost faces neutralised.
    """
    def _log(msg):
        if log:
            log(msg)

    if gmsh_module is None:
        import gmsh as gmsh_module

    g = gmsh_module
    neutralised = 0

    try:
        for _, stag in g.model.getEntities(2):
            x0, y0, z0, x1, y1, z1 = g.model.getBoundingBox(2, stag)
            dz   = abs(z1 - z0)
            diag = ((x1-x0)**2 + (y1-y0)**2) ** 0.5
            if dz > 1e-6 or diag >= threshold_m:
                continue
            try:
                vols = g.model.getAdjacencies(2, stag)[0]
            except Exception:
                continue
            if len(vols) > 0:
                continue

            # Neutralise: force 2 nodes on every boundary curve so TetGen
            # recovers trivial 1-segment edges.  Then set transfinite surface
            # so GMSH meshes it with the minimum possible element count.
            try:
                bcs = g.model.getBoundary([(2, stag)], oriented=False)
                for _, ctag in bcs:
                    try:
                        g.model.mesh.setTransfiniteCurve(abs(ctag), 2)
                    except Exception:
                        pass
                g.model.mesh.setTransfiniteSurface(stag)
                neutralised += 1
            except Exception as exc:
                _log(f"  Ghost face {stag}: neutralise failed: {exc}")

        if neutralised:
            _log(f"  Ghost faces neutralised: {neutralised}  "
                 f"(coplanar faces with no parent volume forced to trivial mesh, "
                 f"threshold {threshold_m*1e3:.2f} mm)")
        else:
            _log(f"  Ghost faces neutralised: 0  (none found)")
    except Exception as exc:
        _log(f"  Ghost face neutralisation error: {exc}")

    return neutralised


# =============================================================================
# Geometry colouring helper
# =============================================================================

def color_geometry():
    """
    Apply layer-based colours to the current GMSH model before display.

    Classification is by surface bounding-box Z centre:
      • Top copper    (F.Cu)  — near board top   → orange  (255,140,  0)
      • Bottom copper (B.Cu)  — near board bottom → blue    ( 30,144,255)
      • Substrate / core       — mid-Z            → tan     (180,140, 80)
      • Port surfaces          — small area, any Z → lime   ( 50,220, 50)

    Volumes:
      • Largest volume (air box) → very transparent white
      • Other volumes            → semi-transparent substrate colour
    """
    g = _gmsh()
    if not g.isInitialized():
        return

    try:
        surfs   = g.model.getEntities(2)
        volumes = g.model.getEntities(3)
        if not surfs:
            return

        # ── collect bounding boxes ────────────────────────────────────────────
        surf_bb  = {}  # tag → (xmin,ymin,zmin,xmax,ymax,zmax)
        for _, tag in surfs:
            try:
                surf_bb[tag] = g.model.getBoundingBox(2, tag)
            except Exception:
                pass

        vol_bb = {}
        for _, tag in volumes:
            try:
                vol_bb[tag] = g.model.getBoundingBox(3, tag)
            except Exception:
                pass

        # ── board Z extents (ignore volumes, just surfaces) ───────────────────
        all_z = []
        for bb in surf_bb.values():
            all_z.extend([bb[2], bb[5]])
        if not all_z:
            return
        z_lo = min(all_z)
        z_hi = max(all_z)
        board_span = z_hi - z_lo
        # Tolerance for "near top/bottom": 15 % of board span, at least 0.1 mm
        z_tol = max(board_span * 0.15, 1e-4)

        # ── surface classification ────────────────────────────────────────────
        COPPER_TOP    = (255, 140,   0, 255)   # orange
        COPPER_BOT    = ( 30, 144, 255, 255)   # dodger-blue
        SUBSTRATE     = (160, 120,  60, 200)   # tan
        PORT_COL      = ( 50, 240,  50, 255)   # bright lime
        AIR_SURF      = (200, 200, 200,  20)   # near-invisible

        # "Port surfaces" heuristic: very small XY footprint (< 2 mm²)
        def _xy_area(bb):
            return (bb[3] - bb[0]) * (bb[4] - bb[1]) * 1e6  # m² → mm²

        # Largest volume volume → air box
        def _vol_extent(bb):
            return ((bb[3]-bb[0])**2 + (bb[4]-bb[1])**2 + (bb[5]-bb[2])**2) ** 0.5

        air_tag = None
        if vol_bb:
            air_tag = max(vol_bb, key=lambda t: _vol_extent(vol_bb[t]))

        for tag, bb in surf_bb.items():
            z_ctr = (bb[2] + bb[5]) * 0.5
            area  = _xy_area(bb)
            if area < 2.0:                          # tiny surface → port
                col = PORT_COL
            elif z_ctr > z_hi - z_tol:             # near top → F.Cu
                col = COPPER_TOP
            elif z_ctr < z_lo + z_tol:             # near bottom → B.Cu
                col = COPPER_BOT
            else:
                col = SUBSTRATE
            try:
                g.model.setColor([(2, tag)], *col)
            except Exception:
                pass

        # ── volume colours ────────────────────────────────────────────────────
        for tag in vol_bb:
            if tag == air_tag:
                try:
                    g.model.setColor([(3, tag)], 200, 200, 255, 8)   # very transparent
                except Exception:
                    pass
            else:
                try:
                    g.model.setColor([(3, tag)], 160, 120, 60, 40)
                except Exception:
                    pass

        # Mark air-box surfaces semi-transparent too
        if air_tag is not None:
            try:
                boundary = g.model.getBoundary([(3, air_tag)], oriented=False)
                for _, stag in boundary:
                    try:
                        g.model.setColor([(2, abs(stag))], *AIR_SURF)
                    except Exception:
                        pass
            except Exception:
                pass

    except Exception as exc:
        print(f"  color_geometry warning: {exc}")


# =============================================================================
# Per-region mesh size constraints
# =============================================================================

def apply_region_mesh_sizes(copper_mm: float = 0.10,
                             component_mm: float = 0.10,
                             air_mm: float = 1.00,
                             substrate_mm: float = 0.30):
    """
    Apply per-region mesh size constraints to the current GMSH model.

    Surfaces are classified by Z bounding-box position and XY footprint:
      • Small XY area (< 2 mm²) — ports / component faces  → component_mm
      • Z clearly outside board (> 25 % board-height beyond top/bottom) → air_mm
      • Z near board top or bottom (within 5 % of board span)  → copper_mm
      • Everything else (substrate interior)                    → substrate_mm

    Sizes are applied via gmsh.model.mesh.setSize() on boundary vertices.
    A size of 0 for any region means that region is left at the global
    CharacteristicLengthMax (no per-region override).
    """
    g = _gmsh()
    if not g.isInitialized():
        return
    # All-zero → nothing to do
    if copper_mm <= 0 and component_mm <= 0 and air_mm <= 0 and substrate_mm <= 0:
        return

    try:
        surfs = g.model.getEntities(2)
        if not surfs:
            return

        surf_bb = {}
        for _, tag in surfs:
            try:
                surf_bb[tag] = g.model.getBoundingBox(2, tag)
            except Exception:
                pass
        if not surf_bb:
            return

        # Board Z extents from all surfaces
        z_lo = min(bb[2] for bb in surf_bb.values())
        z_hi = max(bb[5] for bb in surf_bb.values())
        board_span = z_hi - z_lo
        z_tol   = max(board_span * 0.05, 1e-4)   # copper classification band
        air_tol = board_span * 0.25               # Z this far beyond board → air

        _PORT_AREA_MM2 = 2.0  # mm²

        # Collect the minimum requested size per vertex
        _pt_size: dict[int, float] = {}

        for tag, bb in surf_bb.items():
            z_ctr   = (bb[2] + bb[5]) * 0.5
            xy_area = (bb[3] - bb[0]) * (bb[4] - bb[1]) * 1e6  # m² → mm²

            if xy_area < _PORT_AREA_MM2:
                size = component_mm
            elif z_ctr > z_hi + air_tol or z_ctr < z_lo - air_tol:
                size = air_mm
            elif z_ctr > z_hi - z_tol or z_ctr < z_lo + z_tol:
                size = copper_mm
            else:
                size = substrate_mm

            if size <= 0:
                continue
            size_m = size * 1e-3

            try:
                pts = g.model.getBoundary([(2, tag)], recursive=True)
                for _, pt in pts:
                    pt = abs(pt)
                    if pt not in _pt_size or size_m < _pt_size[pt]:
                        _pt_size[pt] = size_m
            except Exception:
                pass

        if not _pt_size:
            return

        # Group points by size and apply in batches
        from collections import defaultdict
        _by_size: dict[float, list] = defaultdict(list)
        for pt, sz in _pt_size.items():
            _by_size[sz].append((0, pt))

        # Build a set of all currently valid point tags to avoid calling
        # setSize() on orphaned points left behind by removeEntities().
        # setSize() on an orphaned (deleted) point triggers a deferred native
        # crash in GMSH's internal mesh-size field update on a background thread.
        try:
            _valid_pts = {t for _, t in g.model.getEntities(0)}
        except Exception:
            _valid_pts = None  # fall back to no filtering

        n_applied = 0
        for sz, dimtags in sorted(_by_size.items()):
            try:
                if _valid_pts is not None:
                    dimtags = [(d, t) for d, t in dimtags if t in _valid_pts]
                if not dimtags:
                    continue
                g.model.mesh.setSize(dimtags, sz)
                n_applied += len(dimtags)
            except Exception:
                pass

        print(f"  Region mesh sizes applied: {n_applied} vertices "
              f"(Cu={copper_mm:.2f} Comp={component_mm:.2f} "
              f"Sub={substrate_mm:.2f} Air={air_mm:.2f} mm)", flush=True)

    except Exception as exc:
        print(f"  apply_region_mesh_sizes warning: {exc}")


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
        for _opt, _val in [
            ("General.Verbosity",          0),
            ("Geometry.Surfaces",          1),
            ("Geometry.SurfaceType",       2),
            ("Geometry.Points",            0),
            ("Geometry.Lines",             1),
            ("General.AlphaChannelSupport", 1),
            # Top-down view, X→right, Y-up (our model has negative Y so
            # less-negative = top, matching KiCad Y-down orientation).
            ("General.RotationX",          0),
            ("General.RotationY",          0),
            ("General.RotationZ",          0),
        ]:
            try:
                g.option.setNumber(_opt, _val)
            except Exception:
                pass
        color_geometry()
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
        g.option.setNumber("General.RotationX",   0)
        g.option.setNumber("General.RotationY",   0)
        g.option.setNumber("General.RotationZ",   0)
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
