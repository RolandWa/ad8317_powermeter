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


def sanitize_gerber_tiny_segments(src: pathlib.Path, dst: pathlib.Path,
                                  min_seg_um: float = 0.0,
                                  drop_zero_segments: bool = True,
                                  simplify_regions: bool = False,
                                  region_min_seg_um: float = 0.0,
                                  log=None) -> tuple[bool, int]:
    """
    Re-write a Gerber file while downgrading tiny D01 draw segments to D02 moves.

    This keeps pen position continuity (critical for following segments) while
    removing micro-segments that create dense CAD boundaries and slow meshing.

    Args:
        src, dst            : input/output Gerber files
        min_seg_um          : draw segments shorter than this are downgraded
                              from D01 to D02 (0 disables threshold filtering)
        drop_zero_segments  : when True, zero-length D01 draws are downgraded
        simplify_regions    : when True, also simplify tiny D01 edges inside
                      G36/G37 regions by dropping tiny vertex steps
        region_min_seg_um   : threshold used for in-region tiny-step filtering
                      (0 -> use min_seg_um when simplify_regions=True)
        log                 : optional logger callable

    Returns:
        (ok, converted_count)
    """
    def _log(msg):
        if log:
            log(msg)

    if min_seg_um <= 0 and not drop_zero_segments and not simplify_regions:
        # No-op mode: caller can skip this function, but keep a safe fast-path.
        try:
            dst.write_text(src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            return True, 0
        except Exception as exc:
            _log(f"  sanitize_gerber: copy failed for {src.name}: {exc}")
            return False, 0

    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _log(f"  sanitize_gerber: cannot read {src.name}: {exc}")
        return False, 0

    fmt_m = _re.search(r'%FS[LT]A[XY](\d)(\d)[XY]\d\d', text)
    frac_d = int(fmt_m.group(2)) if fmt_m else 6
    scale = 10.0 ** frac_d
    min_seg_mm = max(0.0, float(min_seg_um)) * 1e-3
    region_min_seg_mm = max(0.0, float(region_min_seg_um)) * 1e-3
    if simplify_regions and region_min_seg_mm <= 0:
        region_min_seg_mm = min_seg_mm

    coord_re = _re.compile(r'(?:X(-?\d+))?(?:Y(-?\d+))?D0*([123])\*')
    in_region = False
    out_lines: list[str] = []
    converted = 0

    cur_x = 0.0
    cur_y = 0.0
    eps = 1e-12

    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()

        if line == "G36*":
            in_region = True
            out_lines.append(raw_line)
            continue
        if line == "G37*":
            in_region = False
            out_lines.append(raw_line)
            continue

        m = coord_re.search(line)
        if not m:
            out_lines.append(raw_line)
            continue

        raw_x, raw_y, d = m.group(1), m.group(2), m.group(3)
        next_x = cur_x if raw_x is None else (int(raw_x) / scale)
        next_y = cur_y if raw_y is None else (int(raw_y) / scale)

        # Never touch arc-interpolation lines (I/J terms) or non-draw commands.
        if "I" in line or "J" in line or d != "1":
            out_lines.append(raw_line)
            cur_x, cur_y = next_x, next_y
            continue

        dx = next_x - cur_x
        dy = next_y - cur_y
        seg_len_mm = (dx * dx + dy * dy) ** 0.5
        is_zero = abs(dx) <= eps and abs(dy) <= eps
        # Region and non-region can use different thresholds.
        _thr_mm = region_min_seg_mm if in_region else min_seg_mm
        is_tiny = _thr_mm > 0 and seg_len_mm < _thr_mm

        if in_region:
            if (drop_zero_segments and is_zero) or (simplify_regions and is_tiny):
                # Inside regions, drop tiny vertex steps instead of emitting D02.
                # This keeps region semantics valid while collapsing micro-edges.
                converted += 1
                continue
            out_lines.append(raw_line)
            cur_x, cur_y = next_x, next_y
            continue

        if (drop_zero_segments and is_zero) or is_tiny:
            # Keep coordinate state update but avoid creating a tiny edge.
            new_line = _re.sub(r'D0*1\*', 'D02*', raw_line, count=1)
            out_lines.append(new_line if new_line != raw_line else raw_line)
            converted += 1
        else:
            out_lines.append(raw_line)

        cur_x, cur_y = next_x, next_y

    try:
        dst.write_text("".join(out_lines), encoding="utf-8")
    except Exception as exc:
        _log(f"  sanitize_gerber: cannot write {dst}: {exc}")
        return False, converted

        _log(f"  Gerber sanitize {src.name}: converted {converted} tiny D01 segment(s) "
            f"(min={min_seg_um:.1f} um, drop_zero={drop_zero_segments}, "
            f"simplify_regions={simplify_regions}, region_min={region_min_seg_um:.1f} um)")
    return True, converted


def repair_gerber_region_polygons(src: pathlib.Path, dst: pathlib.Path,
                                   log=None) -> tuple[bool, int]:
    """
    Re-write a Gerber file, repairing degenerate G36/G37 polygon loops.

    Fixes two classes of errors that EMerge's loopsplit.py validator rejects:
      1. Collinear backtracking — a vertex where the polygon reverses direction
         (A→B→A spike). Detected via cross≈0 AND dot<0 on consecutive edge
         vectors; the spike vertex is removed. Repeated until stable.
      2. Self-intersecting outlines — two non-adjacent edges cross. Repaired
         via Shapely Polygon.buffer(0) when shapely is importable; skipped
         (backtracking removal alone applied) when shapely is absent.

    Non-region content (header, apertures, D01/D02/D03 draws) is passed
    through unchanged.  Returns (ok, repaired_count) where repaired_count
    is the number of G36/G37 regions that were modified.
    """
    def _log(msg):
        if log:
            log(msg)

    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        _log(f"  repair_gerber: cannot read {src.name}: {exc}")
        return False, 0

    fmt_m = _re.search(r'%FS[LT]A[XY](\d)(\d)[XY]\d\d', text)
    frac_d = int(fmt_m.group(2)) if fmt_m else 6
    scale = 10.0 ** frac_d

    coord_re = _re.compile(r'(?:X(-?\d+))?(?:Y(-?\d+))?D0*([123])\*')

    def _to_gerber_coord(x_mm: float, y_mm: float, cmd: str) -> str:
        xi = int(round(x_mm * scale))
        yi = int(round(y_mm * scale))
        return f"X{xi}Y{yi}{cmd}*\n"

    def _remove_backtracking(pts: list) -> list:
        """
        Remove collinear-backtrack spike vertices; repeat until stable.

        Threshold matches EMerge's metre-scale check (tol=1e-12 m²) converted to
        mm²: 1e-12 / (0.001)² = 1e-6 mm².  We use 1e-5 (10x slack) to account
        for floating-point noise while still catching all EMerge-flagged vertices.
        """
        EPS_CROSS = 1e-5   # mm² — matches EMerge 1e-12 m² with margin
        changed = True
        while changed and len(pts) >= 3:
            changed = False
            new_pts: list = []
            n = len(pts)
            for i in range(n):
                prev = pts[(i - 1) % n]
                curr = pts[i]
                nxt  = pts[(i + 1) % n]
                ax = curr[0] - prev[0];  ay = curr[1] - prev[1]
                bx = nxt[0]  - curr[0];  by = nxt[1]  - curr[1]
                cross = ax * by - ay * bx
                dot   = ax * bx + ay * by
                if abs(cross) <= EPS_CROSS and dot < 0:
                    changed = True   # skip this backtrack vertex
                else:
                    new_pts.append(curr)
            pts = new_pts
        return pts

    def _orient2d(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    def _edges_cross(a, b, c, d):
        """True if segments AB and CD properly (non-degenerate) cross."""
        o1 = _orient2d(a[0], a[1], b[0], b[1], c[0], c[1])
        o2 = _orient2d(a[0], a[1], b[0], b[1], d[0], d[1])
        o3 = _orient2d(c[0], c[1], d[0], d[1], a[0], a[1])
        o4 = _orient2d(c[0], c[1], d[0], d[1], b[0], b[1])
        return o1 * o2 < 0 and o3 * o4 < 0

    def _repair_self_intersections(pts: list, window: int = 60,
                                   max_iters: int = 40) -> list:
        """
        Remove self-intersecting polygon segments.

        Only checks edge pairs within *window* steps of each other — this covers
        the typical close-proximity crossings from Gerber zone-fill outlines while
        keeping O(n·window·max_iters) rather than O(n²·max_iters).

        When a crossing between edges i→i+1 and j→j+1 is found, the shorter arc
        (vertices i+1 … j inclusive) is deleted, connecting vertex i directly to
        vertex j+1.
        """
        for _ in range(max_iters):
            n = len(pts)
            if n < 4:
                break
            fixed = False
            for i in range(n):
                a = pts[i];  b = pts[(i + 1) % n]
                jmax = min(i + window, n - (1 if i > 0 else 2))
                for j in range(i + 2, jmax):
                    if (j + 1) % n == i:
                        continue   # skip closing / adjacent edge
                    c = pts[j];  d = pts[(j + 1) % n]
                    if _edges_cross(a, b, c, d):
                        # Remove vertices i+1 … j  (the shorter inner segment)
                        pts = pts[:i + 1] + pts[j + 1:]
                        fixed = True
                        break
                if fixed:
                    break
            if not fixed:
                break
        return pts

    def _repair_pts(raw_pts: list) -> tuple[list, bool]:
        """
        Return (repaired_pts, was_modified).
        raw_pts is the full vertex list as collected from the Gerber region,
        possibly including an explicit closing duplicate of the first vertex.
        """
        if len(raw_pts) < 3:
            return raw_pts, False

        # Remove duplicate consecutive vertices
        pts = [raw_pts[0]]
        for p in raw_pts[1:]:
            if abs(p[0] - pts[-1][0]) > 1e-10 or abs(p[1] - pts[-1][1]) > 1e-10:
                pts.append(p)
        # Remove closing duplicate so we work with an open ring
        if (len(pts) > 1 and
                abs(pts[0][0] - pts[-1][0]) < 1e-10 and
                abs(pts[0][1] - pts[-1][1]) < 1e-10):
            pts = pts[:-1]

        if len(pts) < 3:
            return raw_pts, False

        original_len = len(pts)

        # Pass 1 — collinear backtracking removal
        pts = _remove_backtracking(pts)
        if len(pts) < 3:
            return raw_pts, False

        # Pass 2 — self-intersection repair (pure Python, no Shapely required)
        pts = _repair_self_intersections(pts)
        if len(pts) < 3:
            return raw_pts, False

        # Pass 3 — Shapely for any remaining complex self-intersections
        try:
            from shapely.geometry import Polygon as _SPoly
            poly = _SPoly(pts)
            if not poly.is_valid:
                fixed = poly.buffer(0)
                if fixed.geom_type == "MultiPolygon":
                    fixed = max(fixed.geoms, key=lambda g: g.area)
                if fixed.geom_type == "Polygon" and not fixed.is_empty:
                    coords = list(fixed.exterior.coords)[:-1]
                    if len(coords) >= 3:
                        pts = [(x, y) for x, y in coords]
        except ImportError:
            pass   # shapely not available — passes 1+2 only
        except Exception:
            pass   # shapely repair failed — keep pass-1+2 result

        modified = len(pts) != original_len
        return pts, modified

    # ── parse and repair ─────────────────────────────────────────────────────
    lines = text.splitlines(keepends=True)
    out_lines: list[str] = []
    repaired_count = 0
    in_region = False
    region_paths: list[tuple[list[str], list[tuple[float, float]]]] = []
    region_body: list[str] = []   # raw lines for the current D02-started contour
    region_pts:  list      = []   # vertices for the current contour
    cur_x = 0.0
    cur_y = 0.0

    for raw_line in lines:
        line = raw_line.strip()

        if not in_region:
            if line == "G36*":
                in_region = True
                region_paths = []
                region_body = []
                region_pts = []
            else:
                out_lines.append(raw_line)
                m = coord_re.search(line)
                if m:
                    rx, ry = m.group(1), m.group(2)
                    if rx is not None: cur_x = int(rx) / scale
                    if ry is not None: cur_y = int(ry) / scale
        else:
            if line == "G37*":
                in_region = False
                if region_body or region_pts:
                    region_paths.append((region_body, region_pts))
                out_lines.append("G36*\n")
                for path_body, path_pts in region_paths:
                    pts_fixed, modified = _repair_pts(path_pts)
                    if len(pts_fixed) < 3:
                        _log(f"  Gerber polygon repair {src.name}: omitted degenerate "
                             f"sub-contour with {len(pts_fixed)} point(s)")
                    elif modified:
                        repaired_count += 1
                        for j, (px, py) in enumerate(pts_fixed):
                            out_lines.append(_to_gerber_coord(
                                px, py, "D02" if j == 0 else "D01"))
                        out_lines.append(_to_gerber_coord(
                            pts_fixed[0][0], pts_fixed[0][1], "D01"))
                    else:
                        out_lines.extend(path_body)
                out_lines.append("G37*\n")
                region_paths = []
                region_body = []
                region_pts  = []
            else:
                m = coord_re.search(line)
                if m:
                    rx, ry = m.group(1), m.group(2)
                    if rx is not None: cur_x = int(rx) / scale
                    if ry is not None: cur_y = int(ry) / scale
                    if m.group(3) == "2" and region_pts:
                        region_paths.append((region_body, region_pts))
                        region_body = []
                        region_pts = []
                region_body.append(raw_line)
                if m:
                    region_pts.append((cur_x, cur_y))

    try:
        dst.write_text("".join(out_lines), encoding="utf-8")
    except Exception as exc:
        _log(f"  repair_gerber: cannot write {dst}: {exc}")
        return False, repaired_count

    if repaired_count:
        _log(f"  Gerber polygon repair {src.name}: {repaired_count} region(s) fixed "
             f"(collinear backtracking removed; Shapely self-intersection repair applied where available)")
    return True, repaired_count


# ── lazy gmsh import (available after emerge initialises GMSH) ────────────────

def _gmsh():
    import gmsh as _g
    return _g


def _gerber_diagnostics(path: pathlib.Path) -> str:
    """Return compact structural diagnostics for a Gerber artifact."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        commands = _re.findall(r"(?:X-?\d+)?(?:Y-?\d+)?D0*([123])\*", text)
        coords = []
        for line in text.splitlines():
            for x_raw, y_raw in _re.findall(
                    r"X(-?\d+)Y(-?\d+)D0*[123]\*", line):
                coords.append((int(x_raw), int(y_raw)))
        bbox = "n/a"
        if coords:
            xs = [x for x, _ in coords]
            ys = [y for _, y in coords]
            bbox = f"({min(xs)},{min(ys)})–({max(xs)},{max(ys)})"
        return (f"bytes={path.stat().st_size} G36={text.count('G36*')} "
                f"G37={text.count('G37*')} D01={commands.count('1')} "
                f"D02={commands.count('2')} D03={commands.count('3')} "
                f"coord_bbox={bbox}")
    except Exception as exc:
        return f"diagnostics_failed={exc}"


# =============================================================================
# Copper layer loader
# =============================================================================

def load_copper_layers(pcb, stackup: dict, pcb_path: pathlib.Path,
                       gerber_dir: pathlib.Path,
                       circ_segs: int = 64, res_mm: float = 0.05,
                       min_seg_um: float = 0.0,
                       drop_zero_segments: bool = True,
                       simplify_regions: bool = False,
                       region_min_seg_um: float = 0.0,
                       repair_regions: bool = True,
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
        repair_regions : when True (default), G36/G37 polygon loops are
                      repaired before passing to EMerge: collinear backtracking
                      vertices are removed and self-intersecting outlines are
                      fixed via Shapely when available.
        log         : callable(str) for progress messages, or None.

    Returns:
        Number of layers successfully loaded.
    """
    def _log(msg):
        if log:
            log(msg)

    cu_layers = [l for l in stackup.get("layers", []) if l["type"] == "copper"]

    def _layer_index_for_name(layer_name: str, fallback_idx: int) -> int:
        """
        Map KiCad copper layer names to FileBasedPCB indices.

        EMerge FileBasedPCB convention:
          -1 -> F.Cu (top)
           0 -> B.Cu (bottom)
           1..N -> In1.Cu, In2.Cu, ...
        """
        name = str(layer_name or "").strip()
        if name == "F.Cu":
            return -1
        if name == "B.Cu":
            return 0

        m = _re.match(r"^In(\d+)\.Cu$", name)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                pass

        return fallback_idx
    suffix = ", pre-crop=ON)" if sim_bounds else ")"
    _log(f"Loading {len(cu_layers)} copper layer(s) "
         f"(n_circ={circ_segs}, res_mm={res_mm}{suffix}")
    _log(f"  Gerber tiny-segment filter: min_seg_um={min_seg_um:.1f}  "
            f"drop_zero_segments={drop_zero_segments}  "
            f"simplify_regions={simplify_regions}  region_min_seg_um={region_min_seg_um:.1f}  "
            f"repair_regions={repair_regions}")

    pcb_stem = pcb_path.stem if (pcb_path and pcb_path.exists()) else ""
    loaded   = 0

    crop_dir     = gerber_dir / "_cropped"
    sanitize_dir = gerber_dir / "_sanitized"
    repaired_dir = gerber_dir / "_repaired"
    if sim_bounds:
        crop_dir.mkdir(parents=True, exist_ok=True)
    if min_seg_um > 0 or drop_zero_segments:
        sanitize_dir.mkdir(parents=True, exist_ok=True)
    if repair_regions:
        repaired_dir.mkdir(parents=True, exist_ok=True)

    for idx, layer in enumerate(cu_layers):
        layer_t0 = time.monotonic()
        name     = layer["name"]
        em_layer_idx = _layer_index_for_name(name, idx)
        gbr_stem = name.replace(".", "_")
        candidates = [
            gerber_dir / f"{pcb_stem}-{gbr_stem}.gbr",
            gerber_dir / f"{gbr_stem}.gbr",
        ]
        gbr = next((p for p in candidates if p.exists()), None)

        if gbr is None:
            _log(f"  [{idx}] {name} (layer={em_layer_idx}): WARNING — Gerber not found "
                 f"(searched: {[c.name for c in candidates]})")
            continue

        if sim_bounds:
            xmin_m, ymin_m, xmax_m, ymax_m = sim_bounds
            cropped = crop_dir / gbr.name
            crop_t0 = time.monotonic()
            ok = crop_gerber_to_bbox(gbr, cropped,
                                     xmin_m, ymin_m, xmax_m, ymax_m,
                                     log=_log)
            load_path = cropped if ok else gbr
            _log(f"  [{idx}] {name}: crop stage {'done' if ok else 'failed'} "
                 f"({time.monotonic()-crop_t0:.2f} s)")
        else:
            load_path = gbr

        if min_seg_um > 0 or drop_zero_segments:
            sanitized = sanitize_dir / load_path.name
            sanitize_t0 = time.monotonic()
            ok_san, _ = sanitize_gerber_tiny_segments(
                load_path, sanitized,
                min_seg_um=min_seg_um,
                drop_zero_segments=drop_zero_segments,
                simplify_regions=simplify_regions,
                region_min_seg_um=region_min_seg_um,
                log=_log,
            )
            if ok_san:
                load_path = sanitized
            _log(f"  [{idx}] {name}: sanitize stage "
                 f"{'done' if ok_san else 'failed'} ({time.monotonic()-sanitize_t0:.2f} s)")

        if repair_regions:
            repaired = repaired_dir / load_path.name
            repair_t0 = time.monotonic()
            ok_rep, _ = repair_gerber_region_polygons(load_path, repaired, log=_log)
            if ok_rep:
                load_path = repaired
            _log(f"  [{idx}] {name}: repair stage "
                 f"{'done' if ok_rep else 'failed'} ({time.monotonic()-repair_t0:.2f} s)")

        size_kb = load_path.stat().st_size / 1024
        _log(f"  [{idx}] {name} (layer={em_layer_idx}): {load_path.name}  "
             f"({size_kb:.0f} kB)  { _gerber_diagnostics(load_path) }")
        _log(f"  [{idx}] {name} (layer={em_layer_idx}): parsing ...")
        t0 = time.monotonic()
        try:
            pcb.layer_from_file(em_layer_idx, str(load_path),
                                res_mm=res_mm,
                                n_circ_segments=circ_segs)
            _log(f"  [{idx}] {name} (layer={em_layer_idx}): done  "
                 f"({time.monotonic()-t0:.1f} s; layer total={time.monotonic()-layer_t0:.1f} s)")
            loaded += 1
        except Exception as exc:
            # Log the parse exception explicitly for debugging
            _log(f"  [{idx}] {name} (layer={em_layer_idx}): parse error — "
                 f"{type(exc).__name__}: {str(exc)[:200]}")
            # Cropped Gerbers can occasionally produce parser edge-cases on
            # complex copper fills. Retry once with the original full Gerber
            # to keep the pipeline moving — BUT ONLY if no domain scope is set,
            # because falling back to the full Gerber violates sim_bounds constraints.
            retried = False
            if not sim_bounds and load_path != gbr and gbr.exists():
                retried = True
                _log(f"  [{idx}] {name} (layer={em_layer_idx}): no domain scope — "
                     f"retrying full Gerber ({gbr.name})")
                gbr_to_load = gbr
                # Apply the same sanitization to the full Gerber that was applied
                # to the cropped version — removes sub-nm edges that become
                # collinear-backtracking vertices after EMerge's metre-scale check.
                if min_seg_um > 0 or drop_zero_segments:
                    gbr_san2 = sanitize_dir / f"full_{gbr.name}"
                    ok_san2, _ = sanitize_gerber_tiny_segments(
                        gbr, gbr_san2,
                        min_seg_um=min_seg_um,
                        drop_zero_segments=drop_zero_segments,
                        simplify_regions=simplify_regions,
                        region_min_seg_um=region_min_seg_um,
                        log=_log,
                    )
                    if ok_san2:
                        gbr_to_load = gbr_san2
                if repair_regions:
                    gbr_rep = repaired_dir / f"full_{gbr.name}"
                    ok_rep2, _ = repair_gerber_region_polygons(gbr_to_load, gbr_rep, log=_log)
                    if ok_rep2:
                        gbr_to_load = gbr_rep
                _log(f"  [{idx}] {name} (layer={em_layer_idx}): fallback artifact "
                     f"{gbr_to_load.name}  {_gerber_diagnostics(gbr_to_load)}")
                t1 = time.monotonic()
                try:
                    pcb.layer_from_file(em_layer_idx, str(gbr_to_load),
                                        res_mm=res_mm,
                                        n_circ_segments=circ_segs)
                    _log(f"  [{idx}] {name} (layer={em_layer_idx}): done "
                        f"(full Gerber fallback, {gbr_to_load.name})  "
                        f"({time.monotonic()-t1:.1f} s; layer total={time.monotonic()-layer_t0:.1f} s)")
                    loaded += 1
                    continue
                except Exception as exc2:
                    _log(f"  [{idx}] {name} (layer={em_layer_idx}): FAILED on full "
                         f"Gerber fallback — {type(exc2).__name__}: {exc2}")
                    _log(f"  [{idx}] {name}: layer total before failure="
                        f"{time.monotonic()-layer_t0:.1f} s")
                    if log:
                        traceback.print_exc()

            if not retried:
                _log(f"  [{idx}] {name} (layer={em_layer_idx}): FAILED — {exc}")
                if log:
                    traceback.print_exc()

    return loaded


def load_vias_from_drills(pcb, gerber_dir: pathlib.Path,
                          pcb_path: pathlib.Path | None = None,
                          sim_bounds: tuple | None = None,
                          allow_manual_fallback: bool = False,
                          log=None) -> dict:
    """
    Load drill/via definitions from Excellon drill files into *pcb*.

    This complements copper Gerber loading by adding plated through-hole/via
    geometry so vertical current paths exist in the 3D model.

    Returns:
        Summary dict with loaded file count, drill size count, and hole count.
        When sim_bounds=(xmin,ymin,xmax,ymax) is provided in metres, only drill
        hits inside that area of interest are added to geometry.
    """
    def _log(msg):
        if log:
            log(msg)

    def _in_bounds(x_m: float, y_m: float) -> bool:
        if sim_bounds is None:
            return True
        xmin_m, ymin_m, xmax_m, ymax_m = sim_bounds
        return xmin_m <= x_m <= xmax_m and ymin_m <= y_m <= ymax_m

    if not gerber_dir.is_dir():
        _log("  Drill import skipped: Gerber directory missing")
        return {
            "loaded_files": 0,
            "total_holes": 0,
            "drill_size_count": 0,
            "drill_sizes_mm": [],
        }

    pcb_stem = pcb_path.stem if (pcb_path and pcb_path.exists()) else ""
    drill_files = []
    if pcb_stem:
        drill_files.extend(sorted(gerber_dir.glob(f"{pcb_stem}*.drl")))
    drill_files.extend(sorted(gerber_dir.glob("*.drl")))

    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for p in drill_files:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)

    if not ordered:
        _log("  Drill import: no .drl files found")
        return {
            "loaded_files": 0,
            "total_holes": 0,
            "drill_size_count": 0,
            "drill_sizes_mm": [],
        }

    def _parse_excellon_data(drl_path: pathlib.Path) -> tuple[int, set[float], dict[str, list[tuple[float, float]]], dict[str, float], bool]:
        """
        Parse Excellon drill file and estimate:
          - number of hole hits (coordinate commands)
          - set of tool diameters used in the body
          - coordinate list per tool for optional manual via fallback
          - tool diameter table in mm
          - whether the file is plated (PTH) by KiCad file-function comment
        """
        tool_diam_mm: dict[str, float] = {}
        used_sizes: set[float] = set()
        coords_by_tool: dict[str, list[tuple[float, float]]] = {}
        holes = 0
        current_tool: str | None = None
        in_header = True
        plated = True

        try:
            lines = drl_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return 0, set(), {}, {}, True

        tool_def_re = _re.compile(r"^T(\d+)C([0-9]*\.?[0-9]+)$")
        tool_sel_re = _re.compile(r"^T(\d+)$")

        for raw in lines:
            line = raw.strip().upper()
            if not line:
                continue

            if "TFFILEFUNCTION,NONPLATED" in line.replace(".", ""):
                plated = False
            elif "TF.FILEFUNCTION,NONPLATED" in line:
                plated = False

            if line == "%":
                in_header = False
                continue

            mdef = tool_def_re.match(line)
            if mdef:
                tool_diam_mm[mdef.group(1)] = float(mdef.group(2))
                continue

            msel = tool_sel_re.match(line)
            if msel:
                current_tool = msel.group(1)
                continue

            if in_header:
                continue

            # Hole/plunge commands in KiCad decimal Excellon bodies are coordinate lines.
            # Also support slot syntax where line may contain two XY pairs.
            xy_pairs = _re.findall(r"X(-?\d+(?:\.\d+)?)Y(-?\d+(?:\.\d+)?)", line)
            if xy_pairs:
                holes += len(xy_pairs)
                if current_tool is not None and current_tool in tool_diam_mm:
                    used_sizes.add(tool_diam_mm[current_tool])
                    lst = coords_by_tool.setdefault(current_tool, [])
                    for sx, sy in xy_pairs:
                        try:
                            # Excellon is metric decimal in mm here.
                            lst.append((float(sx) * 1e-3, float(sy) * 1e-3))
                        except Exception:
                            pass

        return holes, used_sizes, coords_by_tool, tool_diam_mm, plated

    def _manual_add_vias(coords_by_tool: dict[str, list[tuple[float, float]]],
                         tool_diam_mm: dict[str, float], plated: bool) -> int:
        """Fallback when pcb.vias_from_file ingests zero via records."""
        if not hasattr(pcb, "add_vias"):
            return 0

        added = 0
        for tool, coords in coords_by_tool.items():
            if not coords:
                continue
            diam_mm = tool_diam_mm.get(tool)
            if diam_mm is None or diam_mm <= 0:
                continue
            radius_m = 0.5 * diam_mm * 1e-3
            try:
                # add_vias creates conductive vias (appropriate for plated drills).
                # For non-plated drills we still add them as fallback geometry markers
                # because vias_from_file currently ingests zero records on this setup.
                pcb.add_vias(*coords, radius=radius_m)
                added += len(coords)
            except Exception:
                continue
        return added

    loaded = 0
    total_holes = 0
    drill_sizes_mm: set[float] = set()
    manual_fallback_added = 0
    if sim_bounds is not None:
        _log("  Drill geometry scope: filtered to simulation bounds")
    _log(f"Loading drill files ({len(ordered)}) ...")
    for drl in ordered:
        size_kb = drl.stat().st_size / 1024
        _log(f"  drill: {drl.name}  ({size_kb:.1f} kB)  parsing ...")
        t0 = time.monotonic()
        try:
            vias_before = len(getattr(pcb, "vias", []) or [])
            via_holes_before = len(getattr(pcb, "via_holes", []) or [])
            holes, used_sizes, coords_by_tool, tool_diam_mm, plated = _parse_excellon_data(drl)

            if sim_bounds is not None:
                filtered_coords_by_tool: dict[str, list[tuple[float, float]]] = {}
                for _tool, _coords in coords_by_tool.items():
                    _kept = [(x_m, y_m) for (x_m, y_m) in _coords if _in_bounds(x_m, y_m)]
                    if _kept:
                        filtered_coords_by_tool[_tool] = _kept
                coords_by_tool = filtered_coords_by_tool
                holes = sum(len(v) for v in coords_by_tool.values())
                used_sizes = {tool_diam_mm[t] for t, v in coords_by_tool.items() if v and t in tool_diam_mm}

            total_holes += holes
            drill_sizes_mm.update(used_sizes)

            # Only use the native vias_from_file path when no bounds filter is
            # required; it has no bbox/AOI argument and would ingest whole-board vias.
            if sim_bounds is None:
                pcb.vias_from_file(str(drl))

            vias_after = len(getattr(pcb, "vias", []) or [])
            via_holes_after = len(getattr(pcb, "via_holes", []) or [])
            ingested_delta = (vias_after - vias_before) + (via_holes_after - via_holes_before)

            if allow_manual_fallback and ingested_delta <= 0 and coords_by_tool:
                fb_added = _manual_add_vias(coords_by_tool, tool_diam_mm, plated)
                manual_fallback_added += fb_added
                if fb_added > 0:
                    _log(f"  drill: {drl.name}: via parser fallback added {fb_added} hole(s)")

            _log(
                f"  drill: {drl.name}: done  ({time.monotonic()-t0:.1f} s)"
                f"  holes={holes}  sizes={len(used_sizes)}  ingested={max(ingested_delta, 0)}"
            )
            loaded += 1
        except Exception as exc:
            _log(f"  WARNING: vias_from_file failed for {drl.name}: {exc}")

    return {
        "loaded_files": loaded,
        "total_holes": total_holes,
        "drill_size_count": len(drill_sizes_mm),
        "drill_sizes_mm": sorted(drill_sizes_mm),
        "manual_fallback_holes": manual_fallback_added,
    }


# =============================================================================
# PCB geometry builder + commit
# =============================================================================

def build_and_commit(sim, pcb, board_t: float,
                     xmin: float, ymin: float,
                     xmax: float, ymax: float,
                     port_geos: tuple = (),
                     split_z: bool = True,
                     merge: bool = True,
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

    _log(f"  generate_pcb(split_z={split_z}, merge={merge}) ...")
    t0 = time.monotonic()
    try:
        pcb_vol = pcb.generate_pcb(split_z=split_z, merge=merge)
    except TypeError:
        pcb_vol = pcb.generate_pcb()
    _log(f"    done  ({time.monotonic()-t0:.1f} s)")

    _log("  generate_air() ...")
    t1 = time.monotonic()
    air_vol = pcb.generate_air(height=air_height)
    _log(f"    done  ({time.monotonic()-t1:.1f} s)")

    # Generate explicit via geometries when available. Some FileBasedPCB
    # paths ingest drill data into pcb.vias but do not inject them into the
    # main PCB solid automatically.
    via_geos = []
    try:
        _vias = getattr(pcb, "vias", []) or []
        if _vias and hasattr(pcb, "generate_vias"):
            _vg = pcb.generate_vias(merge=False)
            if isinstance(_vg, list):
                via_geos = [v for v in _vg if v is not None]
            elif _vg is not None:
                via_geos = [_vg]
            if via_geos:
                _log(f"  generate_vias() ... done  ({len(via_geos)} via object(s))")
    except Exception as _via_exc:
        _log(f"  WARNING: generate_vias failed: {_via_exc}")

    # Priority: PCB volumes win over air background (lower number = higher priority)
    if isinstance(pcb_vol, list):
        for _v in pcb_vol:
            _v.prio_set(1)
    else:
        pcb_vol.prio_set(1)
    air_vol.prio_set(5)

    pml = open_pml_region(pml_xy, pml_xy, pml_z)

    _log("  commit_geometry() — fusing CAD solids ...")
    t2 = time.monotonic()
    try:
        sim.commit_geometry(pcb_vol, *via_geos, air_vol, pml, *port_geos)
    except Exception as exc:
        _log(f"  commit_geometry failed after {time.monotonic()-t2:.1f} s: {exc}")
        try:
            g = _gmsh()
            if g.isInitialized():
                n_surfs = len(g.model.getEntities(2))
                n_vols  = len(g.model.getEntities(3))
                _log(f"  GMSH entities after failure: {n_surfs} surfaces, {n_vols} volumes")
        except Exception:
            pass
        if log:
            traceback.print_exc()
        raise
    else:
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

    # Identify slivers — skip Discrete surfaces: they have no CAD parametrisation
    # and cannot be jointly parametrised with CAD surfaces in a compound group.
    # Discrete surfaces are handled separately by remove_ghost_faces().
    sliver_set: set[int] = set()
    n_discrete_skipped = 0
    for tag in all_surf_tags:
        x0, y0, z0, x1, y1, z1 = g.model.getBoundingBox(2, tag)
        if (x1 - x0) < threshold_m and (y1 - y0) < threshold_m:
            try:
                _stype = g.model.getType(2, tag)
            except Exception:
                _stype = ""
            if "Discrete" in str(_stype):
                n_discrete_skipped += 1
                continue
            sliver_set.add(tag)

    if not sliver_set:
        _log(f"  Compound sliver surfaces: 0  (no slivers < {threshold_m*1e3:.2f} mm)"
             + (f"  [{n_discrete_skipped} Discrete skipped]" if n_discrete_skipped else ""))
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
             f"groups  (threshold {threshold_m*1e3:.2f} mm)"
             + (f"  [{n_discrete_skipped} Discrete skipped]" if n_discrete_skipped else ""))
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
    # near_ghost_z_threshold: surfaces thinner than this (in m) with no parent
    # volume are treated as ghost faces.  OCC boolean results on curved copper
    # can produce faces with Z spans of 2-5 µm — well above 1 µm but still
    # coplanar in practice.  10 µm catches these without false-positives on
    # real substrate surfaces (which are ≥35 µm thick).
    _Z_THRESHOLD = 10e-6   # 10 µm
    near_ghost_candidates: list[tuple[int, float, float]] = []  # (tag, dz_um, diag_mm)

    try:
        for _, stag in g.model.getEntities(2):
            x0, y0, z0, x1, y1, z1 = g.model.getBoundingBox(2, stag)
            dz   = abs(z1 - z0)
            diag = ((x1-x0)**2 + (y1-y0)**2) ** 0.5
            if dz > _Z_THRESHOLD or diag >= threshold_m:
                # Track surfaces that are volumeless + small XY but dz just over 1 µm
                if dz <= _Z_THRESHOLD * 5 and diag < threshold_m:
                    try:
                        _vcheck = g.model.getAdjacencies(2, stag)[0]
                        if len(_vcheck) == 0:
                            near_ghost_candidates.append((stag, dz * 1e6, diag * 1e3))
                    except Exception:
                        pass
                continue
            try:
                vols = g.model.getAdjacencies(2, stag)[0]
            except Exception:
                continue
            if len(vols) > 0:
                continue

            # Neutralise: force 2 nodes on every boundary curve so TetGen
            # recovers trivial 1-segment edges.
            #
            # Discrete surfaces have no CAD parametrisation — setCompound and
            # setTransfiniteSurface both require parametric surfaces to work.
            # For Discrete surfaces: per-curve transfinite constraints only.
            # For CAD surfaces:
            #   3-4 curves → setTransfiniteSurface (structured trivial mesh)
            #   other count → setCompound with largest adjacent CAD surface
            try:
                surf_type = g.model.getType(2, stag)
            except Exception:
                surf_type = ""
            is_discrete = "Discrete" in str(surf_type)
            try:
                bcs = g.model.getBoundary([(2, stag)], oriented=False)
                for _, ctag in bcs:
                    try:
                        g.model.mesh.setTransfiniteCurve(abs(ctag), 2)
                    except Exception:
                        pass
                if not is_discrete:
                    if len(bcs) in (3, 4):
                        g.model.mesh.setTransfiniteSurface(stag)
                    else:
                        # Find largest adjacent CAD surface as compound anchor
                        _bc_set = {abs(c[1]) for c in bcs}
                        _best_anchor, _best_area = None, -1.0
                        for _, _as in g.model.getEntities(2):
                            if _as == stag:
                                continue
                            try:
                                _as_type = g.model.getType(2, _as)
                            except Exception:
                                _as_type = ""
                            if "Discrete" in str(_as_type):
                                continue  # don't compound Discrete→Discrete
                            _as_bcs = {abs(c[1]) for c in
                                       g.model.getBoundary([(2, _as)], oriented=False)}
                            if _bc_set & _as_bcs:
                                _bb = g.model.getBoundingBox(2, _as)
                                _area = (_bb[3]-_bb[0]) * (_bb[4]-_bb[1])
                                if _area > _best_area:
                                    _best_area = _area
                                    _best_anchor = _as
                        if _best_anchor is not None:
                            g.model.mesh.setCompound(2, [_best_anchor, stag])
                neutralised += 1
            except Exception as exc:
                _log(f"  Ghost face {stag}: neutralise failed: {exc}")

        if neutralised:
            _log(f"  Ghost faces neutralised: {neutralised}  "
                 f"(coplanar faces with no parent volume forced to trivial mesh, "
                 f"threshold {threshold_m*1e3:.2f} mm, Z<{_Z_THRESHOLD*1e6:.0f} µm)")
        else:
            _log(f"  Ghost faces neutralised: 0  (none found)")

        # Report near-ghost surfaces that had dz just above 1 µm — these are
        # candidates for future mesh failures.
        if near_ghost_candidates:
            _log(f"  Near-ghost surfaces (no volume, XY<{threshold_m*1e3:.2f} mm, "
                 f"dz 1–{_Z_THRESHOLD*1e6*5:.0f} µm — neutralised by transfinite): "
                 f"{len(near_ghost_candidates)}")
            for _ng_tag, _ng_dz, _ng_diag in near_ghost_candidates[:20]:
                _log(f"    surface {_ng_tag}: dz={_ng_dz:.2f} µm  diag={_ng_diag:.4f} mm")
            if len(near_ghost_candidates) > 20:
                _log(f"    ... +{len(near_ghost_candidates)-20} more")

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
    • Port/Lumped surfaces   — by name or small area      → green shades

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
        LUMPED_COL    = ( 40, 190,  60, 255)   # strong green
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

        def _entity_name(dim: int, tag: int) -> str:
            try:
                return str(g.model.getEntityName(dim, tag) or "")
            except Exception:
                return ""

        for tag, bb in surf_bb.items():
            z_ctr = (bb[2] + bb[5]) * 0.5
            area  = _xy_area(bb)
            name_u = _entity_name(2, tag).upper()
            is_named_port = ("PORT" in name_u) or ("LUMPEDPORT" in name_u)
            is_lumped = ("_RC" in name_u) or ("_RL" in name_u) or ("LUMPED" in name_u)

            if is_lumped:
                col = LUMPED_COL
            elif is_named_port:
                col = PORT_COL
            elif area < 2.0:                          # tiny surface → likely port
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
            vname_u = _entity_name(3, tag).upper()
            if ("_RC" in vname_u) or ("_RL" in vname_u) or ("LUMPED" in vname_u):
                try:
                    g.model.setColor([(3, tag)], *LUMPED_COL)
                    continue
                except Exception:
                    pass
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
                             substrate_mm: float = 0.30,
                             copper_z_mm: float = 0.05):
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

    copper_z_mm adds a GMSH Box field centred on each copper Z-band to force
    finer Z-direction elements (anisotropic refinement).  Set to 0 to disable.
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

        # ── Anisotropic Z refinement on copper layers via GMSH Box fields ────
        # setSize() is isotropic; a Box field with VIn=copper_z_mm and a tight
        # Z thickness forces the mesher to place fine elements through the copper
        # layer depth while the XY size is already handled by the vertex sizes above.
        if copper_z_mm > 0 and copper_mm > 0:
            try:
                # Identify unique copper Z-band centres (surfaces already
                # classified as copper_mm above)
                cu_z_bands: set[tuple[float, float]] = set()
                for tag, bb in surf_bb.items():
                    z_ctr = (bb[2] + bb[5]) * 0.5
                    if z_ctr > z_hi - z_tol or z_ctr < z_lo + z_tol:
                        xy_area = (bb[3] - bb[0]) * (bb[4] - bb[1]) * 1e6
                        if xy_area >= _PORT_AREA_MM2:
                            # Round to 4 decimal places to deduplicate layers
                            cu_z_bands.add((round(bb[2], 4), round(bb[5], 4)))

                x_lo = min(bb[0] for bb in surf_bb.values())
                x_hi = max(bb[3] for bb in surf_bb.values())
                y_lo = min(bb[1] for bb in surf_bb.values())
                y_hi = max(bb[4] for bb in surf_bb.values())
                # Expand XY box 5 % beyond board edges so it fully covers fills
                dx = (x_hi - x_lo) * 0.05; dy = (y_hi - y_lo) * 0.05
                x0 = x_lo - dx; x1 = x_hi + dx
                y0 = y_lo - dy; y1 = y_hi + dy

                # VOut must be large (≥ air_mm) so the Box field does NOT
                # constrain element size outside the copper Z band — outside
                # the box GMSH takes the minimum of all active sizes, and a
                # tight VOut would force fine elements across the whole volume.
                v_out = max(air_mm, substrate_mm) * 1e-3  # relax outside box

                field_ids = []
                for z_bot, z_top in cu_z_bands:
                    # Pad Z band slightly so tetrahedra straddling the copper
                    # top/bottom faces also get the fine Z size.
                    pad = copper_z_mm * 0.5e-3
                    fid = g.model.mesh.field.add("Box")
                    g.model.mesh.field.setNumber(fid, "VIn",  copper_z_mm * 1e-3)
                    g.model.mesh.field.setNumber(fid, "VOut", v_out)
                    g.model.mesh.field.setNumber(fid, "XMin", x0)
                    g.model.mesh.field.setNumber(fid, "XMax", x1)
                    g.model.mesh.field.setNumber(fid, "YMin", y0)
                    g.model.mesh.field.setNumber(fid, "YMax", y1)
                    g.model.mesh.field.setNumber(fid, "ZMin", z_bot - pad)
                    g.model.mesh.field.setNumber(fid, "ZMax", z_top + pad)
                    field_ids.append(fid)

                if field_ids:
                    # Set as background mesh so GMSH applies it in addition to
                    # the vertex sizes already set above.  GMSH takes the
                    # minimum of the background field and vertex-prescribed sizes.
                    min_fid = g.model.mesh.field.add("Min")
                    g.model.mesh.field.setNumbers(min_fid, "FieldsList", field_ids)
                    g.model.mesh.field.setAsBackgroundMesh(min_fid)
                    print(f"  Copper Z-refinement: {len(cu_z_bands)} layer(s), "
                          f"VIn={copper_z_mm:.3f} mm  VOut={v_out*1e3:.2f} mm  "
                          f"(Box fields: {field_ids})", flush=True)
            except Exception as _ze:
                print(f"  Copper Z-refinement warning: {_ze}", flush=True)

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

def sim_view(sim, plot_mesh: bool = False, labels: bool = False,
             bc: bool = False, off_screen: bool = False,
             use_gmsh: bool = False,
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
    if "use_gmsh"   in sig_params:                  kwargs["use_gmsh"]  = use_gmsh
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
