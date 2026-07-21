"""
emerge_runner.py — EMerge v2.8.0 FEM model builder, solver, and reporter.

Uses the real emerge API:
  PCBNew  — stackup-aware PCB geometry
  lumped_port_pts()  — port excitation at pad coordinates
  open_pml_region()  — absorbing boundary condition
  sim.mw.set_frequency_range() / run_sweep() — frequency sweep
  generate_touchstone() — write .s2p result

The .kicad_pcb file is the single source of truth for stackup and layer
geometry via kicad_reader.read_stackup() / read_pad_positions().

Debug mode
----------
Set the environment variable EMERGE_DEBUG=1 or pass --debug on the CLI to
enable verbose diagnostic output in the report and on stdout.

Author: Author
Version: 1.2.0
"""

import math
import os
import pathlib
import sys
import textwrap
import time
import traceback

# Force UTF-8 on stdout/stderr — Windows defaults to cp1252 which can't
# encode Greek or other non-ASCII characters that appear in log strings.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

# ── DEBUG flag ────────────────────────────────────────────────────────────────
# Set EMERGE_DEBUG=1 env var, or pass --debug on CLI, to enable.
DEBUG: bool = os.environ.get("EMERGE_DEBUG", "0").strip() not in ("0", "", "false", "False")

def _dbg(msg: str, report_lines: list | None = None):
    """Print a debug line unconditionally (DEBUG mode only at call sites)."""
    line = f"  [DEBUG] {msg}"
    print(line)
    if report_lines is not None:
        report_lines.append(line)

# ── emerge imports ────────────────────────────────────────────────────────────
_EMERGE_OK  = False
_EMERGE_ERR = ""
_EMERGE_VER = ""
_FILE_PCB_OK = False   # True when FileBasedPCB (pygerber) is available

try:
    import emerge
    _EMERGE_VER = getattr(emerge, "__version__", "?")

    # Simulation is at the top level
    from emerge import Simulation

    # open_pml_region, PCBNew, PCBLayer — internal sub-packages
    # (not re-exported at emerge.__init__ in v2.8.0)
    from emerge._emerge.geo.open_region import open_pml_region
    from emerge._emerge.geo.pcb import PCBNew, PCBLayer
    from emerge._emerge.cs import ZAX

    # generate_touchstone — microwave physics sub-package
    from emerge._emerge.physics.microwave.touchstone import generate_touchstone

    # Solver engine classes (linear algebra backend)
    from emerge._emerge.solver import (
        SolverPardiso, SolverMUMPS, SolverCuDSS,
        SolverSuperLU, SolverUMFPACK,
        _PARDISO_AVAILABLE, _MUMPS_AVAILABLE, _CUDSS_AVAILABLE, _UMFPACK_AVAILABLE,
    )

    _EMERGE_OK = True

    # FileBasedPCB — reads actual Gerber copper geometry (requires pygerber).
    # Used instead of PCBNew when Gerber files are present.
    try:
        from emerge.beta.gerber import FileBasedPCB as _FileBasedPCB
        _FILE_PCB_OK = True
    except Exception:
        _FileBasedPCB = None

except ImportError as _e:
    _EMERGE_ERR = str(_e)
    if DEBUG:
        traceback.print_exc()

# ── kicad_reader — sibling module ─────────────────────────────────────────────
try:
    from kicad_reader import (read_pad_positions, read_stackup,
                              read_passive_components,
                              read_board_outline, point_in_board)
except ImportError:
    try:
        from .kicad_reader import (read_pad_positions, read_stackup,
                                   read_passive_components,
                                   read_board_outline, point_in_board)
    except ImportError as _e2:
        def read_pad_positions(p): return {}          # type: ignore
        def read_stackup(p): return {}                # type: ignore
        def read_passive_components(p): return []     # type: ignore
        def read_board_outline(p): return []          # type: ignore
        def point_in_board(x, y, outline): return True  # type: ignore
        if DEBUG:
            print(f"  [DEBUG] kicad_reader not found: {_e2}")


# =========================================================================== #
# Helpers
# =========================================================================== #

def _fr4_material(er: float, tand: float):
    """Create a generic dielectric Material from stackup εr / tanδ."""
    from emerge import Material
    return Material(er=er, tand=tand, name=f"FR4_er{er:.2f}")


def _copper_material(conductivity: float = 5.96e7):
    """Copper with finite conductivity."""
    from emerge import Material
    return Material(cond=conductivity, name="Copper")


# =========================================================================== #
# Passive component value parser
# =========================================================================== #

_SI = {
    'T': 1e12, 'G': 1e9,  'M': 1e6,
    'k': 1e3,  'K': 1e3,
    'm': 1e-3,
    'u': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15,
}

def parse_component_value(ref: str, value_str: str):
    """
    Convert a KiCad component value string to (type_char, SI_float).

    Type is inferred from the reference prefix:
        R* → resistance [Ω]
        C* → capacitance [F]
        L* → inductance [H]

    Handles EIA notation:  "100", "4.7k", "4k7", "10n", "2p2", "4R7",
                            "100nH", "4.7pF", "1MΩ", "0R47"

    Returns None for DNP / NC / unparseable values.
    Skip thresholds:  R=0 (zero-ohm jumper), R>10 MΩ (open),
                      C<0.01 fF, L<0.01 pH.
    """
    import re as _re
    prefix = ref[0].upper() if ref else ""
    if prefix not in ("R", "L", "C"):
        return None

    s = value_str.strip()
    if not s:
        return None
    if _re.match(r'^(DNP|NC|NF|open|short|~|\?)$', s, _re.I):
        return None

    # Normalise
    s = s.replace(",", ".").replace("Ω", "R")
    for mu in ("µ", "μ"):
        s = s.replace(mu, "u")

    # Strip voltage/current rating suffix: "10uF/16V" → "10uF", "4.7uH/1A" → "4.7uH"
    s = _re.sub(r'[/\\][^/\\]*$', '', s).strip()

    # Strip trailing explicit units (H, F, Ohm/ohm, Hz) — leave SI prefix
    s = _re.sub(r'(?i)(ohms?|hertz|hz)$', '', s)
    s = _re.sub(r'(?i)[HhFf]$', '', s).strip()

    # "4R7" — R as decimal separator (always ohms)
    m = _re.match(r'^(\d+)[Rr](\d+)$', s)
    if m:
        val = float(m.group(1)) + float(m.group(2)) / 10**len(m.group(2))
        return (prefix, val) if val else None

    # "100R" / "47R" — trailing R = ohms, no decimal
    m = _re.match(r'^(\d+(?:\.\d*)?)[Rr]$', s)
    if m:
        val = float(m.group(1))
        if val == 0.0:
            return None   # zero-ohm jumper
        return (prefix, val)

    # "0R" / "0R0" — zero-ohm jumper
    if _re.match(r'^0[Rr]0?$', s):
        return None

    # "4k7" / "4n7" / "2p2" — SI prefix as decimal separator
    m = _re.match(r'^(\d+)([TGMkKmunpf])(\d+)$', s)
    if m:
        mult = _SI.get(m.group(2), 1.0)
        val  = (float(m.group(1)) + float(m.group(3)) / 10**len(m.group(3))) * mult
        return (prefix, val)

    # "100", "4.7", "4.7k", "100n", "2.2p", "1M"
    m = _re.match(r'^(\d+(?:\.\d*)?)([TGMkKmunpf]?)$', s)
    if m:
        val = float(m.group(1)) * _SI.get(m.group(2), 1.0)
        # Skip trivial values
        if prefix == "R" and (val == 0.0 or val > 10e6):
            return None
        if prefix == "C" and val < 1e-17:
            return None
        if prefix == "L" and val < 1e-13:
            return None
        return (prefix, val)

    return None


# =========================================================================== #
# Passive element modeler
# =========================================================================== #

class PassiveElementModeler:
    """
    Identify R/L/C components from the .kicad_pcb, parse their values,
    and insert lumped element geometry into a PCBNew / FileBasedPCB model.

    Each component becomes a flat rectangle at the copper-layer Z height,
    assigned an EMerge lumped_element_material that carries the R, L, or C
    value.  The current-flow direction is the pad1→pad2 unit vector.

    Args:
        pcb_obj      : PCBNew or FileBasedPCB instance (model under construction)
        pcb_path     : path to .kicad_pcb (for read_passive_components)
        stackup      : dict from read_stackup()
        skip_refs    : iterable of refdes to exclude (e.g. ["C5", "R3"])
        report_lines : shared log list
        verbose      : print to stdout
    """

    def __init__(self, pcb_obj, pcb_path, stackup,
                 outline_pts=None, skip_refs=None, report_lines=None, verbose=True):
        self.pcb_obj      = pcb_obj
        self.pcb_path     = pcb_path
        self.stackup      = stackup
        self.outline_pts  = outline_pts or []
        self.skip_refs    = set(skip_refs or [])
        self.report_lines = report_lines if report_lines is not None else []
        self.verbose      = verbose

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self) -> int:
        """
        Add lumped elements to the model.  Returns count of elements added.
        """
        if not _EMERGE_OK:
            return 0

        try:
            from emerge import lumped_element_material
        except ImportError:
            self._log("  WARNING: lumped_element_material not in this emerge version — "
                      "passives skipped")
            return 0

        components = read_passive_components(self.pcb_path)
        if not components:
            self._log("  No R/L/C components found in PCB.")
            return 0

        board_t = self.stackup["board_thickness_mm"]  * 1e-3   # m
        cu_t    = self.stackup["copper_thickness_mm"] * 1e-3   # m

        added = skipped = 0
        self._log(f"  Passive components in PCB: {len(components)}")

        for comp in components:
            ref   = comp["ref"]
            value = comp["value"]

            if ref in self.skip_refs:
                self._log(f"    {ref:<8} '{value}'  → skipped (user skip list)")
                skipped += 1
                continue

            # Skip components whose midpoint lies outside the board outline
            if self.outline_pts:
                mx = (comp["pad1_xy"][0] + comp["pad2_xy"][0]) * 0.5
                my = (comp["pad1_xy"][1] + comp["pad2_xy"][1]) * 0.5
                if not point_in_board(mx, my, self.outline_pts):
                    self._log(f"    {ref:<8} '{value}'  → skipped (outside board outline)")
                    skipped += 1
                    continue

            parsed = parse_component_value(ref, value)
            if parsed is None:
                self._log(f"    {ref:<8} '{value}'  → skipped (unparseable / DNP / 0R)")
                skipped += 1
                continue

            comp_type, value_si = parsed

            # Absolute pad centres in metres
            x1, y1 = comp["pad1_xy"][0] * 1e-3, comp["pad1_xy"][1] * 1e-3
            x2, y2 = comp["pad2_xy"][0] * 1e-3, comp["pad2_xy"][1] * 1e-3

            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 1e-6:
                self._log(f"    {ref:<8} pads too close ({length*1e3:.4f} mm) — skipped")
                skipped += 1
                continue

            # Unit vectors: along (pad1→pad2) and perpendicular
            ux, uy =  dx / length,  dy / length
            px, py = -uy,           ux

            # Pad dimensions (in metres)
            pw_m, ph_m = comp["pad_size"][0] * 1e-3, comp["pad_size"][1] * 1e-3

            # Body width: pad dimension perpendicular to current flow
            body_w = (ph_m if abs(ux) >= abs(uy) else pw_m)
            body_w = max(body_w, 0.3e-3)   # floor 0.3 mm

            # Trim rectangle to the inter-pad gap to avoid overlapping the copper
            # pad polygons loaded from Gerber (coplanar overlap → GMSH PLC error).
            # pad_len_along = pad extent parallel to current-flow direction.
            pad_len_along = abs(ux) * pw_m + abs(uy) * ph_m
            margin = pad_len_along * 0.5
            gap_length = length - 2.0 * margin
            if gap_length > 0.05e-3:   # only trim when gap > 50 µm
                gx1 = x1 + ux * margin;  gy1 = y1 + uy * margin
                gx2 = x2 - ux * margin;  gy2 = y2 - uy * margin
                body_length = gap_length
            else:
                gx1, gy1, gx2, gy2 = x1, y1, x2, y2
                body_length = length

            # Z: top copper = board_t, bottom copper = cu_t (just above z=0)
            layer = comp["layer"]
            z = board_t if ("F.Cu" in layer or layer.upper().startswith("F")) else cu_t

            # Rectangle corners: centred on the gap line
            hw = body_w * 0.5
            xs = [gx1 - px*hw, gx2 - px*hw, gx2 + px*hw, gx1 + px*hw]
            ys = [gy1 - py*hw, gy2 - py*hw, gy2 + py*hw, gy1 + py*hw]

            # Cross-sectional area for lumped material (body_w × cu_t)
            area = body_w * cu_t

            # Human-readable value string
            if   comp_type == "R":
                val_str = (f"{value_si/1e6:.3g} MΩ" if value_si >= 1e6 else
                           f"{value_si/1e3:.3g} kΩ" if value_si >= 1e3 else
                           f"{value_si:.3g} Ω")
            elif comp_type == "C":
                val_str = (f"{value_si*1e12:.3g} pF" if value_si < 1e-9 else
                           f"{value_si*1e9:.3g} nF"  if value_si < 1e-6 else
                           f"{value_si*1e6:.3g} µF")
            else:  # L
                val_str = (f"{value_si*1e12:.3g} pH" if value_si < 1e-9 else
                           f"{value_si*1e9:.3g} nH"  if value_si < 1e-6 else
                           f"{value_si*1e6:.3g} µH")

            try:
                mat_name = f"{ref}_{comp_type}{value_si:.3g}"
                kwargs   = {comp_type: float(value_si)}
                lem = lumped_element_material(
                    material_name = mat_name,
                    direction     = (float(ux), float(uy), 0.0),
                    length        = float(body_length),
                    Area          = float(area),
                    **kwargs,
                )
                self.pcb_obj.add_poly(xs=xs, ys=ys, z=z, material=lem, name=ref)

                layer_s = "top" if z == board_t else "bot"
                self._log(
                    f"    {ref:<8} {comp_type}  {val_str:<14}  {layer_s}  "
                    f"({x1*1e3:.2f},{y1*1e3:.2f})→({x2*1e3:.2f},{y2*1e3:.2f}) mm  "
                    f"len={body_length*1e3:.2f} mm  w={body_w*1e3:.2f} mm")
                added += 1

            except Exception as exc:
                self._log(f"    {ref:<8} lumped_element_material failed: {exc}")
                skipped += 1

        self._log(f"  Passives: {added} modeled, {skipped} skipped")
        return added


# =========================================================================== #
# Geometry debug snapshot
# =========================================================================== #

def _sim_view(sim, plot_mesh=False, labels=True, bc=True,
              off_screen=False, screenshot=None):
    """
    Call sim.view() with only the keyword arguments the installed EMerge
    version actually accepts.  Different v2.8.x builds drop or rename params
    (e.g. 'bc', 'labels', 'off_screen', 'screenshot'), so we probe the
    signature rather than hard-coding the call.
    """
    import inspect
    try:
        sig_params = set(inspect.signature(sim.view).parameters.keys())
    except (TypeError, ValueError):
        sig_params = set()

    kwargs = {}
    if not sig_params or "plot_mesh"  in sig_params: kwargs["plot_mesh"]  = plot_mesh
    if "labels"     in sig_params:                    kwargs["labels"]     = labels
    if "bc"         in sig_params:                    kwargs["bc"]         = bc
    if "off_screen" in sig_params and off_screen:     kwargs["off_screen"] = off_screen
    if "screenshot" in sig_params and screenshot:     kwargs["screenshot"] = screenshot

    sim.view(**kwargs)


def _save_geometry_debug(sim, output_dir, report_lines=None):
    """
    Save geometry snapshot files when DEBUG mode is active.

    Attempts (in order):
      1. Off-screen PNG via sim.view(off_screen=True, screenshot=...)
         — instant visual; can be opened in any image viewer
      2. STEP export via gmsh.write() — openable in FreeCAD / GMSH GUI
      3. BREP export (OpenCASCADE native) as fallback

    All files are written to output_dir/geometry_debug.*

    Args:
        sim        : EMerge Simulation object (geometry already committed)
        output_dir : pathlib.Path — destination directory
        report_lines: shared log list
    """
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []

    def _rpt(msg):
        if report_lines is not None:
            report_lines.append(msg)
        if DEBUG:
            print(msg, flush=True)

    # ── 1. Off-screen PNG screenshot ─────────────────────────────────────────
    png_path = out / "geometry_debug.png"
    try:
        _sim_view(sim, plot_mesh=False, labels=True, bc=True,
                  off_screen=True, screenshot=str(png_path))
        if png_path.exists():
            saved.append(("PNG", png_path))
        else:
            _rpt("  [DEBUG] PNG screenshot: sim.view() ran but no file written "
                 "(off_screen/screenshot not supported by this EMerge build)")
    except Exception as exc:
        _rpt(f"  [DEBUG] PNG screenshot failed: {exc}")

    # ── 2. GMSH geometry export (STEP preferred, BREP fallback) ──────────────
    for ext, label in [(".step", "STEP"), (".brep", "BREP"),
                       (".geo_unrolled", "GMSH geo")]:
        geo_path = out / f"geometry_debug{ext}"
        try:
            import gmsh as _gmsh
            _gmsh.write(str(geo_path))
            saved.append((label, geo_path))
            break   # stop after first successful format
        except Exception as exc:
            _rpt(f"  [DEBUG] {label} export failed: {exc}")

    if saved:
        _rpt(f"  [DEBUG] Geometry snapshot ({len(saved)} file(s)):")
        for label, path in saved:
            _rpt(f"    {label}: {path}")
    else:
        _rpt("  [DEBUG] Geometry snapshot: all formats failed — "
             "inspect geometry via the interactive viewer above")


# =========================================================================== #
# EmergeModelBuilder
# =========================================================================== #

class EmergeModelBuilder:
    """
    Build an EMerge FEM model from a KiCad PCB.

    Port positions are resolved from the .kicad_pcb using ref:pad notation.
    Stackup (εr, tanδ, layer thicknesses) is read from the PCB stackup block —
    it is the single source of truth.

    Args:
        pcb_path   : path to .kicad_pcb
        gerber_dir : directory containing exported Gerbers (used for board bounds)
        port_defs  : dict  {"PORT1": {"pad": "J1:1", "R": 50.0, "active": True, ...}}
        report_lines: shared log list
        verbose    : print progress
    """

    def __init__(self, pcb_path, gerber_dir, port_defs,
                 show_geometry=False,
                 use_gerbers=True,
                 model_passives=True, skip_passives=None,
                 report_lines=None, verbose=True):
        self.pcb_path       = pathlib.Path(pcb_path)
        self.gerber_dir     = pathlib.Path(gerber_dir)
        self.port_defs      = port_defs
        self.show_geometry  = show_geometry
        self.use_gerbers    = use_gerbers
        self.model_passives = model_passives
        self.skip_passives  = list(skip_passives or [])
        self.report_lines   = report_lines if report_lines is not None else []
        self.verbose        = verbose

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self):
        """
        Build the EMerge Simulation object with geometry, stackup and ports.
        Returns the Simulation object, or None on failure.
        """
        _SEP = "─" * 60
        self._log(_SEP)
        self._log("Stage 2 / 5 — Build FEM model")
        self._log(_SEP)
        self._log(f"emerge_runner v1.2.0  Python {sys.version.split()[0]}  "
                  f"emerge {_EMERGE_VER or '(not loaded)'}  "
                  f"FileBasedPCB={'yes' if _FILE_PCB_OK else 'no (install pygerber)'}")
        _t0 = time.monotonic()
        if DEBUG:
            _dbg(f"emerge ok={_EMERGE_OK}  file_pcb_ok={_FILE_PCB_OK}  err={_EMERGE_ERR!r}",
                 self.report_lines)
            _dbg(f"pcb_path={self.pcb_path}", self.report_lines)
            _dbg(f"gerber_dir={self.gerber_dir}", self.report_lines)
            _dbg(f"port_defs={self.port_defs}", self.report_lines)

        if not _EMERGE_OK:
            self._log(f"ERROR: emerge not available — {_EMERGE_ERR}")
            return None

        # ── Read stackup ──────────────────────────────────────────────────────
        stackup  = read_stackup(self.pcb_path)
        pad_map  = read_pad_positions(self.pcb_path)

        if DEBUG:
            _dbg(f"stackup={stackup}", self.report_lines)
            _dbg(f"pad_map keys ({len(pad_map)}): {sorted(pad_map)[:20]}", self.report_lines)

        self._log(f"Stackup: {stackup['copper_layers']} layers, "
                  f"h={stackup['board_thickness_mm']:.3f} mm, "
                  f"er={stackup['er']}, tand={stackup['tand']}")

        # ── Resolve port pad coordinates ─────────────────────────────────────
        ports = []
        for pname, pdef in self.port_defs.items():
            key = pdef.get("pad", "")
            if key not in pad_map:
                self._log(f"WARNING: pad '{key}' for {pname} not found in PCB — skipping")
                continue
            x_mm, y_mm = pad_map[key]
            ports.append({
                "name":   pname,
                "x":      x_mm * 1e-3,   # → metres
                "y":      y_mm * 1e-3,
                "R":      float(pdef.get("R", 50.0)),
                "active": bool(pdef.get("active", True)),
            })
            self._log(f"  {pname}: pad={key}  ({x_mm:.3f}, {y_mm:.3f}) mm  "
                      f"R={pdef.get('R',50)} Ω  active={pdef.get('active',True)}")

        if len(ports) < 1:
            self._log("ERROR: No valid ports resolved — cannot build model.")
            return None

        if DEBUG:
            _dbg(f"Resolved ports: {[p['name'] for p in ports]}", self.report_lines)

        # ── Build PCBNew stackup layers ───────────────────────────────────────
        cu_mat  = _copper_material()
        cu_t     = stackup["copper_thickness_mm"] * 1e-3   # m
        board_t  = stackup["board_thickness_mm"]  * 1e-3   # m

        stack_layers = []
        for lyr in stackup["layers"]:
            t = lyr["thick"] * 1e-3
            if t == 0:
                continue
            if lyr["type"] == "copper":
                stack_layers.append(PCBLayer(thickness=t, material=cu_mat,
                                             name=lyr["name"]))
            else:
                mat = _fr4_material(lyr["er"] or stackup["er"],
                                    lyr["tand"] or stackup["tand"])
                stack_layers.append(PCBLayer(thickness=t, material=mat,
                                             name=lyr["name"]))

        # ── Board outline (Edge.Cuts) ─────────────────────────────────────────
        outline_pts = read_board_outline(self.pcb_path)   # mm
        margin = max(0.005, board_t * 3)   # 3× board thickness, min 5 mm

        if outline_pts:
            ol_xs = [p[0] * 1e-3 for p in outline_pts]
            ol_ys = [p[1] * 1e-3 for p in outline_pts]
            xmin, xmax = min(ol_xs) - margin, max(ol_xs) + margin
            ymin, ymax = min(ol_ys) - margin, max(ol_ys) + margin
            bw = (max(ol_xs) - min(ol_xs)) * 1e3
            bh = (max(ol_ys) - min(ol_ys)) * 1e3
            self._log(f"Board outline: {len(outline_pts)} vertices  "
                      f"({min(ol_xs)*1e3:.1f}, {min(ol_ys)*1e3:.1f}) – "
                      f"({max(ol_xs)*1e3:.1f}, {max(ol_ys)*1e3:.1f}) mm  "
                      f"size {bw:.1f} x {bh:.1f} mm")
        else:
            xs_p = [p["x"] for p in ports]
            ys_p = [p["y"] for p in ports]
            xmin, xmax = min(xs_p) - margin, max(xs_p) + margin
            ymin, ymax = min(ys_p) - margin, max(ys_p) + margin
            self._log("Board outline: not found — using port-based bounds")

        dom_w = (xmax - xmin) * 1e3
        dom_h = (ymax - ymin) * 1e3
        self._log(f"Simulation domain: ({xmin*1e3:.1f}, {ymin*1e3:.1f}) – "
                  f"({xmax*1e3:.1f}, {ymax*1e3:.1f}) mm  "
                  f"size {dom_w:.1f} x {dom_h:.1f} mm  margin={margin*1e3:.1f} mm")

        # Warn about ports that fall outside the simulation domain
        for p in ports:
            px_mm, py_mm = p["x"] * 1e3, p["y"] * 1e3
            in_x = xmin * 1e3 <= px_mm <= xmax * 1e3
            in_y = ymin * 1e3 <= py_mm <= ymax * 1e3
            status = "OK" if (in_x and in_y) else "WARNING: OUTSIDE DOMAIN"
            self._log(f"  Port check {p['name']}: ({px_mm:.2f}, {py_mm:.2f}) mm — {status}")
            if not (in_x and in_y):
                self._log(f"    domain X [{xmin*1e3:.1f}, {xmax*1e3:.1f}]  "
                          f"port X={px_mm:.2f}  in={in_x}")
                self._log(f"    domain Y [{ymin*1e3:.1f}, {ymax*1e3:.1f}]  "
                          f"port Y={py_mm:.2f}  in={in_y}")
                self._log(f"    Port {p['name']} is outside the simulation domain — "
                          f"it will NOT be excited. Check pad '{p.get('name', '?')}' "
                          f"placement in KiCad.")

        if DEBUG:
            _dbg(f"Board region (m): x=[{xmin:.4f},{xmax:.4f}] y=[{ymin:.4f},{ymax:.4f}]",
                 self.report_lines)
            _dbg(f"stack_layers ({len(stack_layers)}): "
                 f"{[(l.name, l.thickness) for l in stack_layers]}",
                 self.report_lines)

        # ── Create EMerge Simulation ──────────────────────────────────────────
        model_name = self.pcb_path.stem
        loglevel = "DEBUG" if DEBUG else "WARNING"
        self._log(f"Creating Simulation '{model_name}' (loglevel={loglevel})")
        sim = Simulation(model_name, loglevel=loglevel)
        sim.set_physics(microwave=True, heatconduction=False)

        # Increase curved-boundary arc resolution early — before geometry is
        # committed — to avoid GMSH PLC "segment and facet intersect" errors on
        # PCBs with arc pads or rounded board outlines.
        try:
            sim.mesher.set_curved_boundary_meshing(50)
        except Exception:
            pass

        # FileBasedPCB reads actual Gerber copper traces (accurate, slow ~4 min).
        # PCBNew uses uniform copper planes (fast, seconds).
        # Controlled by use_gerbers config flag; also requires pygerber installed.
        use_gerbers = self.use_gerbers and _FILE_PCB_OK and self.gerber_dir.is_dir()
        if use_gerbers:
            pcb = _FileBasedPCB(
                thickness     = board_t,
                unit          = 1.0,
                stack         = stack_layers if stack_layers else None,
                layers        = stackup["copper_layers"],
                trace_material= cu_mat,
            )
            self._log(f"PCB geometry: FileBasedPCB (Gerbers from {self.gerber_dir})")
        else:
            pcb = PCBNew(
                thickness     = board_t,
                unit          = 1.0,
                stack         = stack_layers if stack_layers else None,
                layers        = stackup["copper_layers"],
                trace_material= cu_mat,
            )
            reason = "pygerber unavailable" if not _FILE_PCB_OK else "no Gerber dir"
            self._log(f"PCB geometry: PCBNew (simplified — {reason})")
        pcb.set_bounds(xmin, ymin, xmax, ymax)

        if DEBUG and self.gerber_dir.is_dir():
            gbr_files = sorted(self.gerber_dir.glob("*.gbr"))
            _dbg(f"Gerber directory ({len(gbr_files)} .gbr files): {self.gerber_dir}",
                 self.report_lines)
            for gf in gbr_files:
                _dbg(f"  {gf.name:<55} {gf.stat().st_size/1024:6.0f} kB", self.report_lines)

        # Load copper layers from Gerber files when FileBasedPCB is active.
        # KiCad kicad-cli names files: {stem}-{LayerName}.gbr, dots → underscores.
        if use_gerbers:
            copper_layers = stackup.get("layers", [])
            cu_layer_names = [l["name"] for l in copper_layers if l["type"] == "copper"]
            self._log(f"Loading {len(cu_layer_names)} copper layer(s) from Gerbers "
                      f"(this can take several minutes for complex boards):")
            layer_idx = 0
            for layer_name in cu_layer_names:
                gbr_stem = layer_name.replace(".", "_")
                candidates = [
                    self.gerber_dir / f"{self.pcb_path.stem}-{gbr_stem}.gbr",
                    self.gerber_dir / f"{gbr_stem}.gbr",
                ]
                gbr_path = next((p for p in candidates if p.exists()), None)
                if gbr_path is None:
                    self._log(f"  Layer {layer_idx} ({layer_name}): "
                              f"WARNING — Gerber not found, will use empty copper plane")
                    if DEBUG:
                        _dbg(f"  Searched: {[str(c) for c in candidates]}", self.report_lines)
                else:
                    size_kb = gbr_path.stat().st_size / 1024
                    self._log(f"  Layer {layer_idx} ({layer_name}): "
                              f"{gbr_path.name}  {size_kb:.0f} kB  — parsing ...")
                    _t_lyr = time.monotonic()
                    try:
                        pcb.layer_from_file(layer_idx, str(gbr_path))
                        self._log(f"  Layer {layer_idx} ({layer_name}): "
                                  f"done  ({time.monotonic()-_t_lyr:.1f} s)")
                    except Exception as exc:
                        self._log(f"  Layer {layer_idx} ({layer_name}): "
                                  f"WARNING — layer_from_file failed: {exc}")
                layer_idx += 1

        # ── Add lumped ports ─────────────────────────────────────────────────
        port_half = max(0.0005, board_t * 0.5)  # half-size of port rectangle
        port_geos = []
        for p in ports:
            p1 = (p["x"] - port_half, p["y"])
            p2 = (p["x"] + port_half, p["y"])
            z_top = board_t
            port_geo = pcb.lumped_port_pts(
                p1=p1, p2=p2,
                z=z_top,
                z_ground=0.0,
                name=p["name"],
            )
            port_geos.append((p, port_geo))
            self._log(f"  Port {p['name']} added at ({p['x']*1e3:.2f}, {p['y']*1e3:.2f}) mm")

        # ── Passive R/L/C lumped elements ────────────────────────────────────
        if self.model_passives:
            self._log("")
            self._log("Modeling passive components (R/L/C) ...")
            passive_modeler = PassiveElementModeler(
                pcb_obj      = pcb,
                pcb_path     = self.pcb_path,
                stackup      = stackup,
                outline_pts  = outline_pts,
                skip_refs    = self.skip_passives,
                report_lines = self.report_lines,
                verbose      = self.verbose,
            )
            passive_modeler.run()

        # ── Generate PCB geometry and PML ────────────────────────────────────
        pml_height = board_t * 4
        board_W    = xmax - xmin
        board_H    = ymax - ymin
        pml_xy     = max(0.005, min(board_W, board_H) * 0.15)
        pml_z      = max(0.005, pml_height * 0.5)

        self._log("")
        self._log("Building 3D geometry ...")
        _t_pcb = time.monotonic()
        pcb_vol = pcb.generate_pcb()
        self._log(f"  generate_pcb()   {time.monotonic()-_t_pcb:6.1f} s")

        _t_air = time.monotonic()
        air_vol = pcb.generate_air(height=pml_height)
        self._log(f"  generate_air()   {time.monotonic()-_t_air:6.1f} s")

        _t_pml = time.monotonic()
        pml = open_pml_region(pml_xy, pml_xy, pml_z)
        self._log(f"  open_pml_region  {time.monotonic()-_t_pml:6.1f} s")

        _t_cmt = time.monotonic()
        self._log(f"  commit_geometry() — fusing CAD solids, may take a while ...")
        sim.commit_geometry(pcb_vol, air_vol, pml,
                            *[geo for _, geo in port_geos])
        self._log(f"  commit_geometry  {time.monotonic()-_t_cmt:6.1f} s")

        # ── Wire up lumped port BCs (v2.8 API) ───────────────────────────────
        # Active ports → LumpedPort (full S-param port, excites during sweep).
        # Passive ports (load) → LumpedPort with load impedance only (port_number
        # still required; solver builds full S-matrix and sweeps all ports).
        for port_num, (p, port_geo) in enumerate(port_geos, start=1):
            sim.mw.bc.LumpedPort(
                face        = port_geo,
                port_number = port_num,
                Z0          = p["R"],
            )

        self._log(f"Model built successfully.  ({time.monotonic()-_t0:.1f} s)")

        if self.show_geometry:
            self._log("Showing 3D geometry — close the viewer window to continue ...")
            try:
                _sim_view(sim, plot_mesh=False, labels=True, bc=True)
                self._log("  Geometry viewer closed — continuing to mesh.")
            except Exception as exc:
                self._log(f"WARNING: geometry viewer failed: {exc}")
                self._log("  Viewer failed (geometry may have an issue).")
                self._log("  Meshing will attempt to continue regardless.")
            # Re-apply curved-boundary resolution — the viewer call may reset
            # GMSH internal state, losing the setting made during model build.
            try:
                sim.mesher.set_curved_boundary_meshing(50)
            except Exception:
                pass

        # Debug: save geometry snapshot (PNG + STEP/BREP) before meshing
        if DEBUG:
            self._log("")
            self._log("Saving geometry debug snapshot ...")
            _save_geometry_debug(sim, self.gerber_dir.parent, self.report_lines)

        return sim


# =========================================================================== #
# EmergeSolver
# =========================================================================== #

class EmergeSolver:
    """
    Run a frequency sweep on an EMerge Simulation and write a Touchstone file.

    Args:
        model      : Simulation object from EmergeModelBuilder.run()
        output_dir : Directory for .s2p output
        freq_start : Start frequency in Hz
        freq_stop  : Stop frequency in Hz
        freq_steps : Number of frequency points
        cells_per_lambda: Mesh density (higher = finer mesh)
        report_lines: shared log list
        verbose    : print progress
    """

    # Solver engine name → class factory.  "auto" lets EMerge pick.
    _SOLVER_MAP = {
        "pardiso": lambda: SolverPardiso() if _EMERGE_OK else None,
        "mumps":   lambda: SolverMUMPS()   if _EMERGE_OK else None,
        "cuda":    lambda: SolverCuDSS()   if _EMERGE_OK else None,
        "cudss":   lambda: SolverCuDSS()   if _EMERGE_OK else None,
        "superlu": lambda: SolverSuperLU() if _EMERGE_OK else None,
        "umfpack": lambda: SolverUMFPACK() if _EMERGE_OK else None,
    }

    def __init__(self, model, output_dir,
                 freq_start=1e6, freq_stop=10e9, freq_steps=201,
                 cells_per_lambda=15, solver_engine="auto",
                 show_mesh=False,
                 report_lines=None, verbose=True):
        self.model            = model
        self.output_dir       = pathlib.Path(output_dir)
        self.freq_start       = freq_start
        self.freq_stop        = freq_stop
        self.freq_steps       = freq_steps
        self.cells_per_lambda = cells_per_lambda
        self.solver_engine    = solver_engine.strip().lower() if solver_engine else "auto"
        self.show_mesh        = show_mesh
        self.report_lines     = report_lines if report_lines is not None else []
        self.verbose          = verbose

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self):
        """
        Solve and export Touchstone. Returns path to .s2p file, or None.
        """
        if self.model is None:
            self._log("ERROR: No model provided to solver.")
            return None

        sim = self.model
        _SEP = "─" * 60
        engine_label = self.solver_engine if self.solver_engine != "auto" else "auto"
        self._log("")
        self._log(_SEP)
        self._log("Stage 3 / 5 — Mesh generation")
        self._log(_SEP)
        self._log(f"  Cells/lambda : {self.cells_per_lambda}")
        self._log(f"  Solver       : {engine_label.upper()}")

        sim.set_resolution(1.0 / self.cells_per_lambda)
        sim.mw.set_frequency_range(
            fmin    = float(self.freq_start),
            fmax    = float(self.freq_stop),
            Npoints = int(self.freq_steps),
        )

        # Apply solver engine if explicitly requested
        if self.solver_engine != "auto":
            factory = self._SOLVER_MAP.get(self.solver_engine)
            if factory is None:
                self._log(f"WARNING: Unknown solver engine '{self.solver_engine}' — using auto")
            else:
                try:
                    solver_obj = factory()
                    sim.mw.solveroutine.set_solver(solver_obj)
                    self._log(f"  Solver engine forced: {self.solver_engine.upper()}")
                except Exception as exc:
                    self._log(f"WARNING: Could not set solver '{self.solver_engine}': {exc}")

        # Increase curved-boundary approximation resolution to avoid GMSH PLC
        # "segment and facet intersect" errors on complex PCB geometry.
        try:
            sim.mesher.set_curved_boundary_meshing(50)
            self._log("  Curved boundary resolution: 50")
        except Exception:
            pass   # older EMerge versions without this API

        self._log("Generating mesh …")
        _t_mesh = time.monotonic()
        sim.generate_mesh()
        self._log(f"  Mesh done.  ({time.monotonic()-_t_mesh:.1f} s)")

        if self.show_mesh:
            self._log("Showing 3D mesh — close the viewer window to continue ...")
            try:
                _sim_view(sim, plot_mesh=True)
            except Exception as exc:
                self._log(f"WARNING: mesh viewer failed: {exc}")

        self._log("")
        self._log(_SEP)
        self._log("Stage 4 / 5 — Running FEM sweep")
        self._log(_SEP)
        self._log(f"  Frequency : {self.freq_start/1e6:.0f} MHz – "
                  f"{self.freq_stop/1e9:.1f} GHz  ({self.freq_steps} points)")
        _t_sweep = time.monotonic()
        self._log("Running FEM sweep …")
        mw_data = sim.mw.run_sweep()
        self._log(f"  Sweep done.  ({time.monotonic()-_t_sweep:.1f} s)")

        # ── Assemble Smat (M × N × N complex) ────────────────────────────────
        freq_axis = mw_data.scalar.axis("freq")
        n_ports   = sim.mw.nports if hasattr(sim.mw, "nports") and sim.mw.nports else 2
        M         = len(freq_axis)
        Smat      = np.zeros((M, n_ports, n_ports), dtype=complex)

        for k, f in enumerate(freq_axis):
            sc = mw_data.scalar.find(freq=f)
            for i in range(n_ports):
                for j in range(n_ports):
                    try:
                        Smat[k, i, j] = sc.S(i + 1, j + 1)
                    except Exception:
                        Smat[k, i, j] = 0.0

        # ── Write Touchstone ──────────────────────────────────────────────────
        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts_path = self.output_dir / f"{sim.modelname}.s2p"

        generate_touchstone(
            filename     = str(ts_path),
            freq         = np.array(freq_axis),
            Smat         = Smat,
            data_format  = "RI",
            funit        = "GHz",
        )

        self._log("")
        self._log(_SEP)
        self._log("Stage 5 / 5 — Results")
        self._log(_SEP)
        self._log(f"Touchstone written: {ts_path}")
        return ts_path


# =========================================================================== #
# EmergeReporter
# =========================================================================== #

class EmergeReporter:
    """
    Parse a Touchstone .s2p file and report insertion loss / return loss
    against configurable thresholds.

    Args:
        touchstone_path  : path to .s2p file
        il_threshold_db  : insertion loss limit (|S21| > threshold → violation)
        rl_threshold_db  : return loss limit    (|S11| < threshold → violation)
        report_lines     : shared log list
        verbose          : print progress
    """

    def __init__(self, touchstone_path,
                 il_threshold_db=3.0, rl_threshold_db=10.0,
                 report_lines=None, verbose=True):
        self.ts_path         = pathlib.Path(touchstone_path)
        self.il_threshold_db = il_threshold_db
        self.rl_threshold_db = rl_threshold_db
        self.report_lines    = report_lines if report_lines is not None else []
        self.verbose         = verbose

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self):
        """
        Parse Touchstone and check thresholds.
        Returns number of violations (0 = all pass, >0 = failures).
        """
        if not self.ts_path.exists():
            self._log(f"ERROR: Touchstone file not found: {self.ts_path}")
            return -1

        ts = emerge.TouchstoneData(str(self.ts_path))
        freqs = ts.f
        violations = 0

        s11_db_min = None
        s21_db_max = None

        for k, f in enumerate(freqs):
            s11 = ts.S(1, 1)[k] if len(freqs) > 1 else ts.S(1, 1)
            s21 = ts.S(2, 1)[k] if len(freqs) > 1 else ts.S(2, 1)

            s11_db = 20 * math.log10(max(abs(s11), 1e-30))
            s21_db = 20 * math.log10(max(abs(s21), 1e-30))

            il_loss = -s21_db
            rl_db   = -s11_db

            if il_loss > self.il_threshold_db:
                violations += 1
            if rl_db < self.rl_threshold_db:
                violations += 1

            if s21_db_max is None or s21_db > s21_db_max:
                s21_db_max = s21_db
                s21_f_best = f
            if s11_db_min is None or s11_db < s11_db_min:
                s11_db_min = s11_db
                s11_f_worst = f

        il_worst = -(s21_db_max or 0)
        rl_best  = -(s11_db_min or 0)

        self._log(f"S21: worst IL = {il_worst:.1f} dB  "
                  f"(threshold {self.il_threshold_db} dB)  "
                  f"{'PASS' if il_worst <= self.il_threshold_db else 'FAIL'}")
        self._log(f"S11: best RL  = {rl_best:.1f} dB  "
                  f"(threshold {self.rl_threshold_db} dB)  "
                  f"{'PASS' if rl_best >= self.rl_threshold_db else 'FAIL'}")
        self._log(f"Total violations: {violations}")

        return violations


# =========================================================================== #
# CLI entry point — invoked as subprocess by emerge_plugin.py
#
# Usage:
#   python emerge_runner.py --job job.json
#
# job.json schema:
#   {
#     "pcb_path":    "...",
#     "gerber_dir":  "...",
#     "output_dir":  "...",
#     "port_defs":   { "PORT1": {"pad":"J1:1","R":50,"active":true}, ... },
#     "sweep":       { "start_hz":1e6, "stop_hz":10e9, "steps":201, "cells_per_lambda":15 },
#     "thresholds":  { "insertion_loss_db":3.0, "return_loss_db":10.0 }
#   }
#
# Writes results back to a JSON file next to job.json:
#   { "ts_path": "...", "violations": 0, "log": [...] }
# =========================================================================== #

if __name__ == "__main__":
    import argparse
    import json

    # ── helper: build a job dict from a PCB file + TOML config ─────────────────

    def _job_from_pcb(pcb_path: str, config_path: str) -> dict:
        """
        Build a job dict directly from a .kicad_pcb + emerge_config.toml.
        Equivalent to what emerge_plugin.py writes before spawning this script.
        """
        try:
            import tomllib                       # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib          # pip install tomli
            except ImportError:
                raise RuntimeError(
                    "TOML support requires Python 3.11+ or: pip install tomli")

        cfg_path = pathlib.Path(config_path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config not found: {cfg_path}")

        with open(cfg_path, "rb") as fh:
            cfg = tomllib.load(fh)

        pcb = pathlib.Path(pcb_path).resolve()
        out_dir = pcb.parent / "emerge_output"

        # Build port_defs from [ports.*] table
        port_defs = {}
        for name, pd in cfg.get("ports", {}).items():
            port_defs[name] = {
                "pad":    pd["pad"],
                "R":      float(pd.get("R", 50.0)),
                "C":      pd.get("C"),
                "active": bool(pd.get("active", True)),
                "dir":    pd.get("dir", "z"),
            }

        sweep         = cfg.get("sweep", {})
        thresholds    = cfg.get("thresholds", {})
        solver        = cfg.get("solver", {})
        visualization = cfg.get("visualization", {})
        passives_cfg  = cfg.get("passives", {})
        gerber_cfg    = cfg.get("gerber", {})

        return {
            "pcb_path":      str(pcb),
            "gerber_dir":    str(out_dir / "gerbers"),
            "output_dir":    str(out_dir),
            "port_defs":     port_defs,
            "sweep":         sweep,
            "thresholds":    thresholds,
            "solver":        solver,
            "visualization": visualization,
            "passives":      passives_cfg,
            "gerber":        {"use_gerbers": bool(gerber_cfg.get("use_gerbers", True))},
        }

    # ── argument parser ─────────────────────────────────────────────────────────

    parser = argparse.ArgumentParser(
        description="EMerge FEM solver — run as KiCad subprocess or standalone",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Modes
            -----
            Plugin mode (called by emerge_plugin.py):
              python emerge_runner.py --job emerge_job.json

            Standalone test mode (direct PCB + config):
              python emerge_runner.py --pcb path/to/board.kicad_pcb
              python emerge_runner.py --pcb board.kicad_pcb --config plugin/emerge_config.toml

            The --pcb mode writes emerge_output/ next to the PCB file.
        """),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job",
                       help="Path to job JSON written by emerge_plugin.py")
    group.add_argument("--pcb",
                       help="Path to .kicad_pcb — standalone test mode")
    parser.add_argument("--config",
                        default=str(pathlib.Path(__file__).parent / "plugin" / "emerge_config.toml"),
                        help="TOML config (only used with --pcb; default: plugin/emerge_config.toml)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable verbose DEBUG output")
    args = parser.parse_args()

    if args.debug:
        os.environ["EMERGE_DEBUG"] = "1"
        import emerge_runner as _self
        _self.DEBUG = True
        print(f"[DEBUG] emerge_runner {sys.argv}")
        print(f"[DEBUG] Python {sys.version}")
        print(f"[DEBUG] emerge ok={_EMERGE_OK}  ver={_EMERGE_VER}  err={_EMERGE_ERR!r}")

    # ── load job ────────────────────────────────────────────────────────────────

    if args.pcb:
        # Standalone mode: derive job from PCB + TOML
        print(f"\nEMerge standalone test")
        print(f"  PCB   : {args.pcb}")
        print(f"  Config: {args.config}")
        print()
        try:
            job = _job_from_pcb(args.pcb, args.config)
        except Exception as exc:
            print(f"FATAL: {exc}")
            sys.exit(1)
        # Write job JSON to emerge_output/ for inspection
        out_dir = pathlib.Path(job["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        job_path   = out_dir / "emerge_job.json"
        result_path = out_dir / "emerge_job.result.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        print(f"  Job written : {job_path}")
        print()
    else:
        # Plugin mode: read pre-written job JSON
        job_path = pathlib.Path(args.job)
        result_path = job_path.with_suffix(".result.json")
        try:
            with open(job_path, encoding="utf-8") as fh:
                job = json.load(fh)
        except Exception as exc:
            result_path.write_text(
                json.dumps({"ts_path": None, "violations": -1,
                            "log": [f"FATAL: cannot read job file: {exc}"]}),
                encoding="utf-8")
            sys.exit(1)

    # ── run pipeline ─────────────────────────────────────────────────────────────

    report_lines = []

    try:
        sweep      = job.get("sweep", {})
        thresholds = job.get("thresholds", {})

        vis_cfg      = job.get("visualization", {})
        solver_cfg   = job.get("solver", {})
        passives_cfg = job.get("passives", {})
        gerber_cfg   = job.get("gerber", {})

        builder = EmergeModelBuilder(
            pcb_path        = job["pcb_path"],
            gerber_dir      = job["gerber_dir"],
            port_defs       = job["port_defs"],
            show_geometry   = bool(vis_cfg.get("show_geometry", False)),
            use_gerbers     = bool(gerber_cfg.get("use_gerbers", True)),
            model_passives  = bool(passives_cfg.get("model_passives", True)),
            skip_passives   = list(passives_cfg.get("skip", [])),
            report_lines    = report_lines,
            verbose         = True,
        )
        model = builder.run()

        if model is None:
            raise RuntimeError("Model build failed.")

        solver = EmergeSolver(
            model            = model,
            output_dir       = job["output_dir"],
            freq_start       = float(sweep.get("start_hz",   1e6)),
            freq_stop        = float(sweep.get("stop_hz",   10e9)),
            freq_steps       = int  (sweep.get("steps",      201)),
            cells_per_lambda = int  (sweep.get("cells_per_lambda", 15)),
            solver_engine    = solver_cfg.get("engine", "auto"),
            show_mesh        = bool(vis_cfg.get("show_mesh", False)),
            report_lines     = report_lines,
            verbose          = True,
        )
        ts_path = solver.run()

        if ts_path is None:
            raise RuntimeError("Solver failed.")

        reporter = EmergeReporter(
            touchstone_path  = ts_path,
            il_threshold_db  = float(thresholds.get("insertion_loss_db", 3.0)),
            rl_threshold_db  = float(thresholds.get("return_loss_db",   10.0)),
            report_lines = report_lines,
            verbose    = True,
        )
        violations = reporter.run()

        result = {
            "ts_path":    str(ts_path),
            "violations": violations,
            "log":        report_lines,
        }

    except Exception as exc:
        result = {
            "ts_path":    None,
            "violations": -1,
            "log":        report_lines + [f"FATAL: {exc}"],
        }

    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.pcb:
        print(f"\n  Result written: {result_path}")
        print(f"  Violations    : {result['violations']}")

    sys.exit(0 if result["violations"] >= 0 else 1)
