"""
emerge_runner.py — EMerge v2.8.0 FEM pipeline orchestrator.

Responsibilities
----------------
  • Read the job JSON written by emerge_plugin.py (or build it from a
    .kicad_pcb + TOML when run standalone with --pcb).
  • Resolve port pad coordinates from the KiCad PCB.
  • Orchestrate the four pipeline stages:
        EmergeModelBuilder  — geometry + ports
        EmergeSolver        — mesh + FEM sweep + Touchstone export
        EmergeReporter      — pass/fail against thresholds
  • Write a result JSON back to the plugin.

Geometry building, Gerber loading, meshing helpers, and R/L/C element
modelling are in separate library modules:

    gerber_builder.py   — load_copper_layers(), build_and_commit(),
                          fix_sliver_faces(), gmsh_view_*, sim_view(),
                          save_geometry_debug()
    passive_modeler.py  — parse_component_value(), PassiveElementModeler

Debug mode
----------
Set env var EMERGE_DEBUG=1 or pass --debug on the CLI.

Author: Author
Version: 2.0.0
"""

import math
import os
import pathlib
import sys
import textwrap
import time
import traceback
import contextlib

# Force UTF-8 on stdout/stderr (Windows defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

try:
    import psutil as _psutil
except Exception:
    _psutil = None

# ── DEBUG flag ─────────────────────────────────────────────────────────────────
DEBUG: bool = os.environ.get("EMERGE_DEBUG", "0").strip() not in ("0", "", "false", "False")

def _dbg(msg: str, report_lines: list | None = None):
    line = f"  [DEBUG] {msg}"
    print(line)
    if report_lines is not None:
        report_lines.append(line)


def _system_usage_snapshot() -> str:
    """Return a short CPU and memory status string for heartbeat logs."""
    if _psutil is None:
        return "CPU n/a  MEM n/a"

    try:
        cpu_pct = _psutil.cpu_percent(interval=None)
        mem = _psutil.virtual_memory()
        mem_used_mib = mem.used / (1024 * 1024)
        mem_total_mib = mem.total / (1024 * 1024)
        mem_pct = mem.percent
        return f"CPU {cpu_pct:.0f}%  MEM {mem_pct:.0f}% {mem_used_mib:.0f}/{mem_total_mib:.0f} MiB"
    except Exception:
        return "CPU n/a  MEM n/a"


@contextlib.contextmanager
def _suppress_native_output():
    """Temporarily silence native stdout/stderr noise from GUI backends."""
    if not hasattr(os, "dup") or not hasattr(os, "dup2"):
        yield
        return

    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)
    try:
        with open(os.devnull, "w") as devnull:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        try:
            os.dup2(stdout_fd, 1)
        finally:
            os.dup2(stderr_fd, 2)
            os.close(stdout_fd)
            os.close(stderr_fd)


# ── emerge imports ─────────────────────────────────────────────────────────────
_EMERGE_OK   = False
_EMERGE_ERR  = ""
_EMERGE_VER  = ""
_FILE_PCB_OK = False

try:
    import emerge
    _EMERGE_VER = getattr(emerge, "__version__", "?")

    # Version-pin check: warn when installed emerge differs from requirements.txt.
    try:
        import importlib.metadata as _ilm
        _installed_ver = _ilm.version("emerge")
        _req_file = pathlib.Path(__file__).parent / "requirements.txt"
        _req_ver  = None
        if _req_file.exists():
            for _rl in _req_file.read_text(encoding="utf-8").splitlines():
                _rl = _rl.strip()
                if _rl.startswith("emerge=="):
                    _req_ver = _rl.split("==", 1)[1]; break
        if _req_ver and _installed_ver != _req_ver:
            print(f"  [WARNING] emerge version mismatch: installed={_installed_ver}  "
                  f"required={_req_ver}  (see sim/emerge/requirements.txt)")
    except Exception:
        pass

    from emerge import Simulation
    from emerge import lumped_element_material as _lumped_element_material
    from emerge._emerge.geo.pcb      import PCBNew, PCBLayer
    from emerge._emerge.cs           import ZAX
    from emerge._emerge.physics.microwave.touchstone import generate_touchstone
    from emerge._emerge.solver import (
        SolverPardiso, SolverMUMPS, SolverCuDSS,
        SolverSuperLU, SolverUMFPACK,
        _PARDISO_AVAILABLE, _MUMPS_AVAILABLE,
        _CUDSS_AVAILABLE,   _UMFPACK_AVAILABLE,
    )
    _EMERGE_OK = True

    try:
        from emerge.beta.gerber import FileBasedPCB as _BetaFileBasedPCB
        _FILE_PCB_OK = True
    except Exception:
        _BetaFileBasedPCB = None

    try:
        from emerge import parallel_impedance as _parallel_impedance
        from emerge import series_impedance   as _series_impedance
    except Exception:
        _parallel_impedance = None
        _series_impedance   = None

except ImportError as _e:
    _EMERGE_ERR = str(_e)
    if DEBUG:
        traceback.print_exc()


# ── kicad_reader ───────────────────────────────────────────────────────────────
try:
    from kicad_reader import (read_pad_positions, read_stackup,
                              read_passive_components,
                              read_board_outline, read_keepout_bbox,
                              point_in_board)
except ImportError:
    try:
        from .kicad_reader import (read_pad_positions, read_stackup,
                                   read_passive_components,
                                   read_board_outline, read_keepout_bbox,
                                   point_in_board)
    except ImportError as _e2:
        def read_pad_positions(p): return {}
        def read_stackup(p): return {}
        def read_passive_components(p): return []
        def read_board_outline(p): return []
        def read_keepout_bbox(p): return None
        def point_in_board(x, y, outline): return True
        if DEBUG:
            print(f"  [DEBUG] kicad_reader not found: {_e2}")


# ── library modules (extracted helpers) ───────────────────────────────────────
try:
    from gerber_builder import (
        load_copper_layers,
        load_vias_from_drills,
        build_and_commit,
        fix_sliver_faces,
        compound_sliver_surfaces,
        remove_ghost_faces,
        apply_region_mesh_sizes,
        gmsh_view_geometry,
        gmsh_view_mesh,
        sim_view   as _sim_view,
        save_geometry_debug as _save_geometry_debug,
    )
    _GERBER_BUILDER_OK = True
except ImportError:
    try:
        from .gerber_builder import (
            load_copper_layers, build_and_commit, fix_sliver_faces,
            compound_sliver_surfaces, remove_ghost_faces, apply_region_mesh_sizes,
            gmsh_view_geometry, gmsh_view_mesh,
            sim_view   as _sim_view,
            save_geometry_debug as _save_geometry_debug,
        )
        _GERBER_BUILDER_OK = True
    except ImportError as _e3:
        _GERBER_BUILDER_OK = False
        def apply_region_mesh_sizes(*a, **kw): pass  # stub
        def remove_ghost_faces(*a, **kw): return 0   # stub
        if DEBUG:
            print(f"  [DEBUG] gerber_builder not found: {_e3}")

try:
    from passive_modeler import PassiveElementModeler, parse_component_value
    _PASSIVE_MODELER_OK = True
except ImportError:
    try:
        from .passive_modeler import PassiveElementModeler, parse_component_value
        _PASSIVE_MODELER_OK = True
    except ImportError as _e4:
        _PASSIVE_MODELER_OK = False
        def parse_component_value(*a, **kw): return None  # stub
        if DEBUG:
            print(f"  [DEBUG] passive_modeler not found: {_e4}")


# ── material helpers ───────────────────────────────────────────────────────────

def _fr4_material(er: float, tand: float):
    from emerge import Material
    return Material(er=er, tand=tand, name=f"FR4_er{er:.2f}")


def _copper_material(conductivity: float = 5.96e7):
    from emerge import Material
    return Material(cond=conductivity, name="Copper")


# =============================================================================
# EmergeModelBuilder
# =============================================================================

class EmergeModelBuilder:
    """
    Build an EMerge FEM model from a KiCad PCB.

    Port positions are resolved from the .kicad_pcb via ref:pad notation.
    Stackup (εr, tanδ, layer thicknesses) is read from the PCB stackup block.

    Gerber loading  → gerber_builder.load_copper_layers()
    Geometry build  → gerber_builder.build_and_commit()
    Sliver fix      → gerber_builder.fix_sliver_faces()
    Passive R/L/C   → passive_modeler.PassiveElementModeler
    """

    def __init__(self, pcb_path, gerber_dir, port_defs,
                 show_geometry=False,
                 geometry_viewer="auto",
                 use_gerbers=True,
                 model_passives=True, skip_passives=None,
                 curved_boundary_resolution=200,
                 max_mesh_size_mm=0.0, min_mesh_size_mm=0.0,
                 port_focus_only=False,
                 use_keepout_bbox=True,
                 domain_margin_mm=0.0,
                 port_focus_margin_mm=0.0,
                 simplify_geometry=False,
                 simplify_factor=1.0,
                 pcb_split_z=True,
                 pcb_merge=True,
                 gerber_circ_segments=64,
                 gerber_min_circ_segments=24,
                 gerber_res_mm=0.05,
                 gerber_min_segment_um=0.0,
                 gerber_drop_zero_segments=True,
                 gerber_simplify_regions=False,
                 gerber_region_min_segment_um=0.0,
                 gerber_repair_regions=True,
                 sliver_threshold_mm=0.10,
                 report_lines=None, verbose=True):
        self.pcb_path                   = pathlib.Path(pcb_path)
        self.gerber_dir                 = pathlib.Path(gerber_dir)
        self.port_defs                  = port_defs
        self.show_geometry              = show_geometry
        self.geometry_viewer            = str(geometry_viewer or "auto").strip().lower()
        self.use_gerbers                = use_gerbers
        self.model_passives             = model_passives
        self.skip_passives              = list(skip_passives or [])
        self.curved_boundary_resolution = int(curved_boundary_resolution)
        self.max_mesh_size_mm           = float(max_mesh_size_mm)
        self.min_mesh_size_mm           = float(min_mesh_size_mm)
        self.port_focus_only            = bool(port_focus_only)
        self.use_keepout_bbox           = bool(use_keepout_bbox)
        self.domain_margin_mm           = float(domain_margin_mm)
        self.port_focus_margin_mm       = float(port_focus_margin_mm)
        self.simplify_geometry          = bool(simplify_geometry)
        self.simplify_factor            = max(1.0, float(simplify_factor))
        self.pcb_split_z                = bool(pcb_split_z)
        self.pcb_merge                  = bool(pcb_merge)
        self.gerber_circ_segments       = int(gerber_circ_segments)
        self.gerber_min_circ_segments   = max(4, int(gerber_min_circ_segments))
        self.gerber_res_mm              = float(gerber_res_mm)
        self.gerber_min_segment_um      = max(0.0, float(gerber_min_segment_um))
        self.gerber_drop_zero_segments  = bool(gerber_drop_zero_segments)
        self.gerber_simplify_regions    = bool(gerber_simplify_regions)
        self.gerber_region_min_segment_um = max(0.0, float(gerber_region_min_segment_um))
        self.gerber_repair_regions      = bool(gerber_repair_regions)
        self.sliver_threshold_mm        = float(sliver_threshold_mm)
        self.report_lines               = report_lines if report_lines is not None else []
        self.verbose                    = verbose

        # Sanity-check: every attribute used in run() must be set here.
        _REQUIRED = [
            "pcb_path", "gerber_dir", "port_defs", "show_geometry",
            "geometry_viewer",
            "use_gerbers", "model_passives", "skip_passives",
            "curved_boundary_resolution", "max_mesh_size_mm", "min_mesh_size_mm",
            "port_focus_only",
            "use_keepout_bbox",
            "domain_margin_mm", "port_focus_margin_mm",
            "simplify_geometry", "simplify_factor",
            "pcb_split_z", "pcb_merge",
            "gerber_circ_segments", "gerber_min_circ_segments", "gerber_res_mm",
            "gerber_min_segment_um", "gerber_drop_zero_segments",
            "gerber_simplify_regions", "gerber_region_min_segment_um",
            "gerber_repair_regions",
            "sliver_threshold_mm",
            "report_lines", "verbose",
        ]
        _missing = [a for a in _REQUIRED if not hasattr(self, a)]
        if _missing:
            raise AttributeError(
                f"EmergeModelBuilder.__init__ is missing assignments for: {_missing}"
            )

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self):
        """
        Build the EMerge Simulation with geometry, stackup and ports.
        Returns the Simulation object, or None on failure.
        """
        _SEP = "─" * 60
        self._log(_SEP)
        self._log("Stage 2 / 5 — Build FEM model")
        self._log(_SEP)
        self._log(f"emerge_runner v2.0.0  Python {sys.version.split()[0]}  "
                  f"emerge {_EMERGE_VER or '(not loaded)'}  "
                  f"FileBasedPCB={'yes' if _FILE_PCB_OK else 'no'}")
        _t0 = time.monotonic()

        if DEBUG:
            _dbg(f"emerge ok={_EMERGE_OK}  file_pcb_ok={_FILE_PCB_OK}  err={_EMERGE_ERR!r}",
                 self.report_lines)

        if not _EMERGE_OK:
            self._log(f"ERROR: emerge not available — {_EMERGE_ERR}")
            return None

        # ── Stackup + pad positions ───────────────────────────────────────────
        stackup = read_stackup(self.pcb_path)
        pad_map = read_pad_positions(self.pcb_path)

        self._log(f"Stackup: {stackup['copper_layers']} layers, "
                  f"h={stackup['board_thickness_mm']:.3f} mm, "
                  f"er={stackup['er']}, tand={stackup['tand']}")

        # ── Resolve port pad coordinates ──────────────────────────────────────
        ports = []
        for pname, pdef in self.port_defs.items():
            key = pdef.get("pad", "")
            if key not in pad_map:
                self._log(f"WARNING: pad '{key}' for {pname} not found — skipping")
                continue
            x_mm, y_mm = pad_map[key]
            ports.append({
                "name":   pname,
                "x":       x_mm * 1e-3,
                "y":      -y_mm * 1e-3,   # KiCad Y-down → Gerber Y-up (same flip as outline).
                "R":      float(pdef.get("R", 50.0)),
                "C":      float(pdef.get("C") or 0.0),
                "L":      float(pdef.get("L") or 0.0),
                "active": bool(pdef.get("active", True)),
            })
            self._log(f"  {pname}: pad={key}  ({x_mm:.3f}, {-y_mm:.3f}) mm [Gerber Y]  "
                      f"R={pdef.get('R',50)} Ω  active={pdef.get('active',True)}")

        if not ports:
            self._log("ERROR: No valid ports resolved — cannot build model.")
            return None

        outline_pts = read_board_outline(self.pcb_path)
        outline_bbox = None
        if outline_pts:
            xs_o = [p[0] for p in outline_pts]
            ys_o = [p[1] for p in outline_pts]
            outline_bbox = (min(xs_o), min(ys_o), max(xs_o), max(ys_o))

        keepout_bbox = read_keepout_bbox(self.pcb_path)

        # ── Build stackup layers ──────────────────────────────────────────────
        cu_mat  = _copper_material()
        board_t = stackup["board_thickness_mm"] * 1e-3

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

        # ── Board outline → simulation domain ─────────────────────────────────
        # NOTE: keep the area-of-interest in PCB coordinates (mm) so the
        # imported Gerber crop and the generated air box stay aligned with
        # the actual board outline / keepout region.
        _auto_margin = max(0.005, board_t * 3)
        _auto_port_margin = max(0.001, board_t)
        margin = (self.domain_margin_mm * 1e-3) if self.domain_margin_mm > 0 else _auto_margin
        port_margin = (self.port_focus_margin_mm * 1e-3) if self.port_focus_margin_mm > 0 else _auto_port_margin

        self._log(
            f"Domain margins: global={margin*1e3:.1f} mm "
            f"(cfg={self.domain_margin_mm:.1f})  "
            f"port_focus={port_margin*1e3:.1f} mm "
            f"(cfg={self.port_focus_margin_mm:.1f})"
        )

        if self.port_focus_only:
            xs_p = [p["x"] for p in ports]
            ys_p = [p["y"] for p in ports]
            xmin = min(xs_p) - port_margin;  xmax = max(xs_p) + port_margin
            ymin = min(ys_p) - port_margin;  ymax = max(ys_p) + port_margin
            self._log("Domain source: port_focus_only=true — using port-based bounds")
        elif self.use_keepout_bbox and keepout_bbox is not None:
            kx0, ky0, kx1, ky1 = keepout_bbox
            xmin = kx0 * 1e-3
            xmax = kx1 * 1e-3
            ymin = -ky1 * 1e-3   # KiCad Y-down -> Gerber Y-up.
            ymax = -ky0 * 1e-3
            self._log(f"Domain source: keepout bbox ({kx0:.1f}, {ky0:.1f}) – "
                      f"({kx1:.1f}, {ky1:.1f}) mm")
        elif outline_bbox is not None:
            ox0, oy0, ox1, oy1 = outline_bbox
            xmin = ox0 * 1e-3
            xmax = ox1 * 1e-3
            ymin = -oy1 * 1e-3   # KiCad Y-down -> Gerber Y-up.
            ymax = -oy0 * 1e-3
            self._log(f"Domain source: board outline bbox ({ox0:.1f}, {oy0:.1f}) – "
                      f"({ox1:.1f}, {oy1:.1f}) mm")
        else:
            xs_p = [p["x"] for p in ports]
            ys_p = [p["y"] for p in ports]
            xmin = min(xs_p) - port_margin;  xmax = max(xs_p) + port_margin
            ymin = min(ys_p) - port_margin;  ymax = max(ys_p) + port_margin
            if outline_bbox is not None:
                ox0, oy0, ox1, oy1 = outline_bbox
                xmin = ox0 * 1e-3
                xmax = ox1 * 1e-3
                ymin = -oy1 * 1e-3
                ymax = -oy0 * 1e-3
                self._log("Domain source: board outline bbox (keepout disabled)")
            else:
                self._log("Domain source: outline not found — using port-based bounds")

        # Expand domain to include every port (handles off-board components)
        for p in ports:
            if p["x"] - margin < xmin: xmin = p["x"] - margin
            if p["x"] + margin > xmax: xmax = p["x"] + margin
            if p["y"] - margin < ymin: ymin = p["y"] - margin
            if p["y"] + margin > ymax: ymax = p["y"] + margin

        self._log(f"Simulation domain: ({xmin*1e3:.1f}, {ymin*1e3:.1f}) – "
                  f"({xmax*1e3:.1f}, {ymax*1e3:.1f}) mm  margin={margin*1e3:.1f} mm")

        # ── Simulation domain as PCB-coordinate bbox (for passive filtering) ─
        # This rectangle is passed to PassiveElementModeler so only components
        # inside the simulation area are modelled — not the whole board.
        _sd_xmin_mm =  xmin * 1e3
        _sd_xmax_mm =  xmax * 1e3
        _sd_ymin_mm = -ymax * 1e3   # Gerber Y-up → KiCad Y-down: negate.
        _sd_ymax_mm = -ymin * 1e3
        sim_domain_outline = [
            (_sd_xmin_mm, _sd_ymin_mm),
            (_sd_xmax_mm, _sd_ymin_mm),
            (_sd_xmax_mm, _sd_ymax_mm),
            (_sd_xmin_mm, _sd_ymax_mm),
        ]
        self._log(f"Passive filter bbox (KiCad mm): "
                  f"({_sd_xmin_mm:.1f}, {_sd_ymin_mm:.1f}) – "
                  f"({_sd_xmax_mm:.1f}, {_sd_ymax_mm:.1f})")

        # ── Create Simulation (must be BEFORE FileBasedPCB) ───────────────────
        loglevel = "DEBUG" if DEBUG else "WARNING"
        self._log(f"Creating Simulation '{self.pcb_path.stem}' (loglevel={loglevel})")
        sim = Simulation(self.pcb_path.stem, loglevel=loglevel)
        sim.set_physics(microwave=True, heatconduction=False)

        # ── Create PCB object ─────────────────────────────────────────────────
        use_gerbers = self.use_gerbers and _FILE_PCB_OK and self.gerber_dir.is_dir()
        if use_gerbers:
            pcb = _BetaFileBasedPCB(
                thickness      = board_t,
                unit           = 1.0,
                stack          = stack_layers or None,
                layers         = stackup["copper_layers"],
                trace_material = cu_mat,
            )
            self._log(f"PCB geometry: FileBasedPCB (Gerbers from {self.gerber_dir})")
        else:
            pcb = PCBNew(
                thickness      = board_t,
                unit           = 1.0,
                stack          = stack_layers or None,
                layers         = stackup["copper_layers"],
                trace_material = cu_mat,
            )
            reason = "pygerber unavailable" if not _FILE_PCB_OK else "no Gerber dir"
            self._log(f"PCB geometry: PCBNew (simplified — {reason})")
        pcb.set_bounds(xmin, ymin, xmax, ymax)

        # ── Load copper layers from Gerbers ───────────────────────────────────
        if use_gerbers:
            self._log("")
            self._log("Loading copper layers from Gerbers ...")
            if _GERBER_BUILDER_OK:
                _eff_circ = self.gerber_circ_segments
                _eff_res = self.gerber_res_mm
                if self.simplify_geometry:
                    _eff_circ = max(self.gerber_min_circ_segments,
                                    int(round(self.gerber_circ_segments / self.simplify_factor)))
                    _eff_res = self.gerber_res_mm * self.simplify_factor
                    self._log(
                        f"  Geometry simplification: ON  factor={self.simplify_factor:.2f}  "
                        f"circ_segs {self.gerber_circ_segments}->{_eff_circ}  "
                        f"res_mm {self.gerber_res_mm:.3f}->{_eff_res:.3f}"
                    )
                else:
                    self._log("  Geometry simplification: OFF")

                loaded_layers = load_copper_layers(
                    pcb        = pcb,
                    stackup    = stackup,
                    pcb_path   = self.pcb_path,
                    gerber_dir = self.gerber_dir,
                    circ_segs  = _eff_circ,
                    res_mm     = _eff_res,
                    min_seg_um = self.gerber_min_segment_um,
                    drop_zero_segments = self.gerber_drop_zero_segments,
                    simplify_regions = self.gerber_simplify_regions,
                    region_min_seg_um = self.gerber_region_min_segment_um,
                    repair_regions = self.gerber_repair_regions,
                    sim_bounds = (xmin, ymin, xmax, ymax),
                    log        = self._log,
                )
                if self.port_focus_only:
                    self._log("  Geometry scope: port_focus_only=true")
                else:
                    self._log("  Geometry scope: keepout-first (fallback to ports)")

                # Only fail-fast when copper Gerbers are actually present but
                # parsing/loading is incomplete. Unit tests and fallback flows
                # may intentionally run with an empty gerber_dir.
                pcb_stem = self.pcb_path.stem if self.pcb_path.exists() else ""
                present_copper = 0
                for lyr in stackup.get("layers", []):
                    if lyr.get("type") != "copper":
                        continue
                    gbr_stem = str(lyr.get("name", "")).replace(".", "_")
                    candidates = [
                        self.gerber_dir / f"{pcb_stem}-{gbr_stem}.gbr",
                        self.gerber_dir / f"{gbr_stem}.gbr",
                    ]
                    if any(p.exists() for p in candidates):
                        present_copper += 1

                if present_copper > 0 and loaded_layers < present_copper:
                    self._log(
                        f"ERROR: copper Gerber load incomplete "
                        f"({loaded_layers}/{present_copper} present layers). "
                        "Aborting before generate_pcb/commit_geometry to avoid "
                        "invalid solids after parser failures."
                    )
                    return None

                drill_stats = load_vias_from_drills(
                    pcb=pcb,
                    gerber_dir=self.gerber_dir,
                    pcb_path=self.pcb_path,
                    sim_bounds=(xmin, ymin, xmax, ymax),
                    log=self._log,
                )
                if drill_stats.get("loaded_files", 0) > 0:
                    self._log(
                        "  Drill/via files loaded: "
                        f"{drill_stats.get('loaded_files', 0)}; "
                        f"drill sizes: {drill_stats.get('drill_size_count', 0)}; "
                        f"total holes: {drill_stats.get('total_holes', 0)}; "
                        f"fallback added: {drill_stats.get('manual_fallback_holes', 0)}"
                    )
            else:
                self._log("  WARNING: gerber_builder not available — "
                          "falling back to empty copper planes")

        # ── Lumped port geometry ──────────────────────────────────────────────
        import inspect as _inspect
        try:
            _lpp_params = set(_inspect.signature(pcb.lumped_port_pts).parameters.keys())
        except Exception:
            _lpp_params = set()

        port_half = max(0.0005, board_t * 0.5)
        port_geos = []
        for p in ports:
            p1 = (p["x"] - port_half, p["y"])
            p2 = (p["x"] + port_half, p["y"])
            kw = {"p1": p1, "p2": p2, "z": board_t, "z_ground": 0.0}
            if "name" in _lpp_params:
                kw["name"] = p["name"]
            pg = pcb.lumped_port_pts(**kw)
            for attr in ("name", "label", "_name"):
                try:     setattr(pg, attr, p["name"]); break
                except Exception: pass
            # R||C load polygon — adds frequency-dependent reactive load for ports with C defined
            if p["C"] > 0.0:
                try:
                    _hw   = port_half
                    _ztop = getattr(pcb, "top", board_t)
                    _lem  = _lumped_element_material(
                        material_name = f"{p['name']}_RC",
                        direction     = (0.0, 0.0, 1.0),
                        length        = board_t,
                        Area          = (2.0 * _hw) ** 2,
                        R             = p["R"],
                        C             = p["C"],
                    )
                    pcb.add_poly(
                        xs = [p["x"]-_hw, p["x"]+_hw, p["x"]+_hw, p["x"]-_hw],
                        ys = [p["y"]-_hw, p["y"]-_hw, p["y"]+_hw, p["y"]+_hw],
                        z  = _ztop,
                        material = _lem,
                        name     = f"{p['name']}_RC",
                    )
                    self._log(f"  Port {p['name']}: added R={p['R']:.0f} Ω ∥ C={p['C']*1e12:.3g} pF load polygon")
                except Exception as _exc:
                    self._log(f"  WARNING: {p['name']} RC load polygon failed: {_exc}")
            # Series R+jωL load polygon for ports with L defined
            elif p["L"] > 0.0:
                try:
                    from emsutil.material import FreqDependent as _FreqDep, Material as _Mat
                    from emsutil.lib import EPS0 as _EPS0
                    import numpy as _np, math as _math
                    _hw   = port_half
                    _ztop = getattr(pcb, "top", board_t)
                    _d    = board_t
                    _A    = (2.0 * _hw) ** 2
                    _R_v  = p["R"]
                    _L_v  = p["L"]
                    _dv   = _np.array([0.0, 0.0, 1.0])
                    _dvout = _np.outer(_dv, _dv)
                    def _fer_RL(f, _R=_R_v, _L=_L_v, _d=_d, _A=_A):
                        w = 2.0 * _math.pi * f
                        # Z = R + jωL → εr = d / (ε0·A·jω·Z)
                        er_s = _d / (_EPS0 * _A * 1j * w * (_R + 1j * w * _L))
                        return _dvout * (er_s - 1.0) + _np.eye(3)
                    _lem = _Mat(er=_FreqDep(matrix=_fer_RL), name=f"{p['name']}_RL")
                    pcb.add_poly(
                        xs = [p["x"]-_hw, p["x"]+_hw, p["x"]+_hw, p["x"]-_hw],
                        ys = [p["y"]-_hw, p["y"]-_hw, p["y"]+_hw, p["y"]+_hw],
                        z  = _ztop,
                        material = _lem,
                        name     = f"{p['name']}_RL",
                    )
                    self._log(f"  Port {p['name']}: added R={p['R']:.0f} Ω + L={p['L']*1e9:.3g} nH series load polygon")
                except Exception as _exc:
                    self._log(f"  WARNING: {p['name']} RL load polygon failed: {_exc}")
            port_geos.append((p, pg))
            self._log(f"  Port {p['name']} at ({p['x']*1e3:.2f}, {p['y']*1e3:.2f}) mm")

        # ── Passive R/L/C elements ────────────────────────────────────────────
        if self.model_passives:
            self._log("")
            self._log("Modeling passive components (R/L/C) ...")
            if _PASSIVE_MODELER_OK:
                PassiveElementModeler(
                    pcb_obj      = pcb,
                    pcb_path     = self.pcb_path,
                    stackup      = stackup,
                    outline_pts  = sim_domain_outline,   # clip to sim domain, not full board
                    skip_refs    = self.skip_passives,
                    report_lines = self.report_lines,
                    verbose      = self.verbose,
                ).run()
            else:
                self._log("  WARNING: passive_modeler not available — passives skipped")

        # ── 3D geometry + PML + commit ────────────────────────────────────────
        self._log("")
        self._log("Building 3D geometry ...")
        if _GERBER_BUILDER_OK:
            build_and_commit(
                sim       = sim,
                pcb       = pcb,
                board_t   = board_t,
                xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
                port_geos = tuple(pg for _, pg in port_geos),
                split_z   = self.pcb_split_z,
                merge     = self.pcb_merge,
                log       = self._log,
            )
            _sliver_m = self.sliver_threshold_mm * 1e-3
            # Order matters:
            #  1. Remove ghost faces first (coplanar Discrete faces with no parent
            #     volume).  They cause TetGen edge-recovery failure and must be gone
            #     before the other passes scan the model.
            #  2. Fix remaining slivers (set mesh sizes on boundary vertices).
            #  3. Compound remaining slivers with adjacent valid surfaces.
            remove_ghost_faces(threshold_m=_sliver_m, log=self._log)
            fix_sliver_faces(sim=sim, threshold_m=_sliver_m, log=self._log)
            # setCompound-based sliver grouping can create virtual OCC surfaces
            # that become stale after ghost-face cleanup on some boards.
            # Keep this opt-in for stability.
            _enable_compound = os.environ.get("EMERGE_ENABLE_COMPOUND_SLIVERS", "0").strip() in ("1", "true", "yes", "on")
            if _enable_compound:
                compound_sliver_surfaces(threshold_m=_sliver_m, log=self._log)
            else:
                self._log("  Compound sliver surfaces: disabled (set EMERGE_ENABLE_COMPOUND_SLIVERS=1 to enable)")
        else:
            # Inline fallback when gerber_builder is missing
            from emerge._emerge.geo.open_region import open_pml_region
            board_w = xmax - xmin
            board_h = ymax - ymin
            pml_h   = board_t * 4
            pml_xy  = max(0.005, min(board_w, board_h) * 0.15)
            pml_z   = max(0.005, pml_h * 0.5)
            self._log(f"  PCB solid build: split_z={self.pcb_split_z}  merge={self.pcb_merge}")
            try:
                pcb_vol = pcb.generate_pcb(split_z=self.pcb_split_z, merge=self.pcb_merge)
            except TypeError:
                pcb_vol = pcb.generate_pcb()
            air_vol = pcb.generate_air(height=pml_h)
            # Priority: PCB volumes win over air background
            if isinstance(pcb_vol, list):
                for _v in pcb_vol:
                    _v.prio_set(1)
            else:
                pcb_vol.prio_set(1)
            air_vol.prio_set(5)
            pml     = open_pml_region(pml_xy, pml_xy, pml_z)
            sim.commit_geometry(pcb_vol, air_vol, pml,
                                *[pg for _, pg in port_geos])

        # Curved boundary resolution (set after commit — GMSH resets at commit)
        cbr = self.curved_boundary_resolution
        try:
            sim.mesher.set_curved_boundary_meshing(cbr)
            self._log(f"  Curved boundary resolution: {cbr} (post-commit)")
        except Exception:
            pass

        # ── Wire up lumped port boundary conditions ────────────────────────────
        for port_num, (p, port_geo) in enumerate(port_geos, start=1):
            lp_name = f"LumpedPort_{port_num}"
            port_name = str(p.get("name", f"PORT{port_num}"))

            # Keep port geometry tied to user-facing port names (PORT1/PORT2/...).
            for attr in ("name", "label", "_name"):
                try:
                    setattr(port_geo, attr, port_name)
                except Exception:
                    pass

            lp_kwargs = {
                "face": port_geo,
                "port_number": port_num,
                "Z0": p["R"],
            }

            # EMerge pybind often reports LumpedPort(*args, **kwargs), so
            # signature probing is unreliable. Try explicit naming kwargs in order.
            lp_bc = None
            for _k in ("name", "label"):
                try:
                    _kw = dict(lp_kwargs)
                    _kw[_k] = lp_name
                    lp_bc = sim.mw.bc.LumpedPort(**_kw)
                    break
                except TypeError:
                    continue
                except Exception:
                    continue
            if lp_bc is None:
                lp_bc = sim.mw.bc.LumpedPort(**lp_kwargs)

            for attr in ("name", "label", "_name"):
                try:
                    setattr(lp_bc, attr, lp_name)
                except Exception:
                    pass

        self._log(f"Model built successfully.  ({time.monotonic()-_t0:.1f} s)")

        # Optional interactive geometry viewer (blocks until closed)
        if self.show_geometry:
            self._log("Showing 3D geometry — close the viewer window to continue ...")
            mode = self.geometry_viewer
            allow_gmsh = mode in ("auto", "gmsh", "both")
            allow_emerge = mode in ("auto", "emerge", "native", "both")
            shown = False
            emerge_failed = False

            if allow_gmsh and _GERBER_BUILDER_OK:
                try:
                    gmsh_view_geometry("Geometry — close window to continue")
                    shown = True
                except Exception as exc:
                    self._log(f"WARNING: GMSH geometry viewer failed: {exc}")

            if allow_emerge and (mode != "auto" or not shown):
                try:
                    _sim_view(sim, plot_mesh=False, labels=True, bc=False,
                              use_gmsh=True)
                    shown = True
                except Exception as exc:
                    emerge_failed = True
                    self._log(f"WARNING: EMerge geometry viewer failed: {exc}")

            # Fallback: if EMerge viewer was selected and failed, try GMSH.
            if (not shown) and emerge_failed and _GERBER_BUILDER_OK:
                try:
                    self._log("  Falling back to GMSH geometry viewer ...")
                    gmsh_view_geometry("Geometry (fallback) — close window to continue")
                    shown = True
                except Exception as exc:
                    self._log(f"WARNING: GMSH geometry fallback failed: {exc}")

            if not shown:
                self._log("WARNING: no geometry viewer backend available")

        # Debug snapshot (PNG + STEP/BREP)
        if DEBUG:
            self._log("\nSaving geometry debug snapshot ...")
            if _GERBER_BUILDER_OK:
                _save_geometry_debug(sim, self.gerber_dir.parent,
                                     self.report_lines, verbose=True)

        return sim


# =============================================================================
# EmergeSolver
# =============================================================================

class EmergeSolver:
    """
    Run a frequency sweep on an EMerge Simulation and write a Touchstone file.
    """

    _SOLVER_MAP = {
        "pardiso": lambda: SolverPardiso("") if _EMERGE_OK else None,
        "mumps":   lambda: SolverMUMPS("")   if _EMERGE_OK else None,
        "cuda":    lambda: SolverCuDSS("")   if _EMERGE_OK else None,
        "cudss":   lambda: SolverCuDSS("")   if _EMERGE_OK else None,
        "superlu": lambda: SolverSuperLU("") if _EMERGE_OK else None,
        "umfpack": lambda: SolverUMFPACK("") if _EMERGE_OK else None,
    }

    def __init__(self, model, output_dir,
                 freq_start=1e6, freq_stop=10e9, freq_steps=201,
                 cells_per_lambda=15, solver_engine="auto",
                 show_mesh=False,
                 mesh_viewer="auto",
                 show_field_animation=False,
                 field_component="Ez",
                 field_animation_freq_hz=0.0,
                 export_sparam_png=False,
                 export_field_html=False,
                 curved_boundary_resolution=200,
                 max_mesh_size_mm=0.0, min_mesh_size_mm=0.0,
                 # GMSH algorithm
                 algorithm_2d=6, algorithm_3d=10, smoothing=10,
                 max_mesh_retries=8,
                 # CharacteristicLengthMax derivation
                 artifact_threshold_um=50.0,
                 char_length_max_floor_mm=0.15,
                 char_length_max_ceil_mm=0.50,
                 char_length_max_factor=0.80,
                 # per-region mesh sizes (mm; 0 = inherit global CLmax)
                 mesh_copper_mm=0.10,
                 mesh_copper_z_mm=0.05,
                 mesh_component_mm=0.10,
                 mesh_substrate_mm=0.30,
                 mesh_air_mm=1.00,
                 sliver_threshold_mm=0.10,
                 report_lines=None, verbose=True):
        self.model                      = model
        self.output_dir                 = pathlib.Path(output_dir)
        self.freq_start                 = freq_start
        self.freq_stop                  = freq_stop
        self.freq_steps                 = freq_steps
        self.cells_per_lambda           = cells_per_lambda
        self.solver_engine              = (solver_engine or "auto").strip().lower()
        self.show_mesh                  = show_mesh
        self.mesh_viewer                = str(mesh_viewer or "auto").strip().lower()
        self.show_field_animation       = bool(show_field_animation)
        self.field_component            = str(field_component or "Ez")
        self.field_animation_freq_hz    = float(field_animation_freq_hz or 0.0)
        self.export_sparam_png          = bool(export_sparam_png)
        self.export_field_html          = bool(export_field_html)
        self.curved_boundary_resolution = int(curved_boundary_resolution)
        self.max_mesh_size_mm           = float(max_mesh_size_mm)
        self.min_mesh_size_mm           = float(min_mesh_size_mm)
        self.algorithm_2d               = int(algorithm_2d)
        self.algorithm_3d               = int(algorithm_3d)
        self.smoothing                  = int(smoothing)
        self.max_mesh_retries           = int(max_mesh_retries)
        self.artifact_threshold_um      = float(artifact_threshold_um)
        self.char_length_max_floor_mm   = float(char_length_max_floor_mm)
        self.char_length_max_ceil_mm    = float(char_length_max_ceil_mm)
        self.char_length_max_factor     = float(char_length_max_factor)
        self.mesh_copper_mm             = float(mesh_copper_mm)
        self.mesh_copper_z_mm           = float(mesh_copper_z_mm)
        self.mesh_component_mm          = float(mesh_component_mm)
        self.mesh_substrate_mm          = float(mesh_substrate_mm)
        self.mesh_air_mm                = float(mesh_air_mm)
        self.sliver_threshold_mm        = float(sliver_threshold_mm)
        self.report_lines               = report_lines if report_lines is not None else []
        self.verbose                    = verbose

        # Sanity-check: every attribute used in run() must be set here.
        _REQUIRED = [
            "model", "output_dir", "freq_start", "freq_stop", "freq_steps",
            "cells_per_lambda", "solver_engine", "show_mesh", "mesh_viewer",
            "show_field_animation", "field_component", "field_animation_freq_hz",
            "export_sparam_png", "export_field_html",
            "curved_boundary_resolution", "max_mesh_size_mm", "min_mesh_size_mm",
            "algorithm_2d", "algorithm_3d", "smoothing", "max_mesh_retries",
            "artifact_threshold_um", "char_length_max_floor_mm",
            "char_length_max_ceil_mm", "char_length_max_factor",
            "mesh_copper_mm", "mesh_copper_z_mm", "mesh_component_mm",
            "mesh_substrate_mm", "mesh_air_mm", "sliver_threshold_mm",
            "report_lines", "verbose",
        ]
        _missing = [a for a in _REQUIRED if not hasattr(self, a)]
        if _missing:
            raise AttributeError(
                f"EmergeSolver.__init__ is missing assignments for: {_missing}"
            )

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def _result_dir(self) -> pathlib.Path:
        return pathlib.Path(self.output_dir)

    def _resolve_field_frequency(self, freq_axis) -> float:
        if len(freq_axis) == 0:
            raise RuntimeError("frequency axis is empty")
        if self.field_animation_freq_hz > 0:
            target_f = float(self.field_animation_freq_hz)
        else:
            target_f = float(freq_axis[len(freq_axis) // 2])
        freq_arr = np.array(freq_axis, dtype=float)
        return float(freq_arr[np.argmin(np.abs(freq_arr - target_f))])

    def _export_sparam_png(self, freq_axis, Smat):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:
            self._log(f"WARNING: S-parameter PNG export skipped: {exc}")
            return None

        try:
            out_path = self._result_dir() / "S_params.png"
            freqs_ghz = np.array(freq_axis, dtype=float) / 1e9
            fig, ax = plt.subplots(figsize=(8, 4))
            for port_idx in range(min(Smat.shape[1], 4)):
                try:
                    ax.plot(freqs_ghz, 20*np.log10(np.abs(Smat[:, port_idx, 0]) + 1e-30), label=f"S{port_idx+1}1")
                except Exception:
                    pass
            ax.set_xlabel("Frequency (GHz)")
            ax.set_ylabel("Magnitude (dB)")
            ax.set_title(f"{self.model.modelname} S-parameters")
            ax.grid(True)
            ax.legend()
            fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
            plt.close(fig)
            self._log(f"S-parameter PNG written: {out_path}")
            return out_path
        except Exception as exc:
            self._log(f"WARNING: S-parameter PNG export failed: {exc}")
            return None

    def _export_field_html(self, mw_data, freq_axis):
        try:
            import plotly.graph_objects as go
        except Exception as exc:
            self._log(f"WARNING: field HTML export skipped: {exc}")
            return None

        try:
            if not hasattr(mw_data, "field"):
                raise RuntimeError("field data not available in sweep result")
            nearest_f = self._resolve_field_frequency(freq_axis)
            fld = mw_data.field.find(freq=nearest_f)
            ds_cp = 0.3e-3

            def _cutplane_surface(plane, coord, name, colorscale="Hot"):
                kwargs = {plane: coord}
                eh_cp = fld.cutplane(ds_cp, **kwargs)
                try:
                    pd = eh_cp.scalar(self.field_component, "abs")
                except Exception:
                    pd = eh_cp.scalar("normE", "abs")
                X, Y, Z, F = pd.xyzf
                F = np.nan_to_num(np.abs(F))
                return go.Surface(
                    x=X*1e3, y=Y*1e3, z=Z*1e3,
                    surfacecolor=F,
                    colorscale=colorscale, opacity=0.78,
                    showscale=(plane == "z"),
                    colorbar=dict(title=f"|{self.field_component}|", x=1.02) if plane == "z" else None,
                    name=name, showlegend=True,
                )

            cp_traces = [
                _cutplane_surface("z", 0.0, f"XY z=0 mm @ {nearest_f/1e9:.4g} GHz"),
                _cutplane_surface("y", 0.0, "XZ y=0 mm", colorscale="Plasma"),
                _cutplane_surface("x", 0.0, "YZ x=0 mm", colorscale="Viridis"),
            ]
            fig_cp = go.Figure(data=cp_traces)
            fig_cp.update_layout(
                title=f"{self.model.modelname} {self.field_component} field view",
                scene=dict(
                    xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)",
                    aspectmode="data",
                    camera=dict(eye=dict(x=1.4, y=-1.6, z=1.1)),
                ),
                margin=dict(l=0, r=0, t=40, b=0),
                legend=dict(x=0.01, y=0.99),
            )
            out_path = self._result_dir() / "field_cutplane.html"
            fig_cp.write_html(str(out_path), include_plotlyjs="cdn")
            self._log(f"Field HTML written: {out_path}")
            return out_path
        except Exception as exc:
            self._log(f"WARNING: field HTML export failed: {exc}")
            return None

    def run(self):
        """Solve and export Touchstone. Returns path to .s2p file, or None."""
        if self.model is None:
            self._log("ERROR: No model provided to solver.")
            return None

        sim  = self.model
        _SEP = "─" * 60

        self._log("")
        self._log(_SEP)
        self._log("Stage 3 / 5 — Mesh generation")
        self._log(_SEP)
        self._log(f"  Cells/lambda : {self.cells_per_lambda}")
        self._log(f"  Solver       : {self.solver_engine.upper()}")

        sim.set_resolution(1.0 / self.cells_per_lambda)
        sim.mw.set_frequency_range(
            fmin    = float(self.freq_start),
            fmax    = float(self.freq_stop),
            Npoints = int(self.freq_steps),
        )

        if self.solver_engine != "auto":
            factory = self._SOLVER_MAP.get(self.solver_engine)
            if factory is None:
                self._log(f"WARNING: Unknown solver '{self.solver_engine}' — using auto")
            else:
                try:
                    sim.mw.solveroutine.set_solver(factory())
                    self._log(f"  Solver engine forced: {self.solver_engine.upper()}")
                except Exception as exc:
                    self._log(f"WARNING: Could not set solver: {exc}")

        cbr = self.curved_boundary_resolution
        try:
            sim.mesher.set_curved_boundary_meshing(cbr)
            self._log(f"  Curved boundary resolution: {cbr}")
        except Exception:
            pass

        if self.max_mesh_size_mm > 0:
            try:
                sim.mesher.set_max_meshsize(self.max_mesh_size_mm * 1e-3)
            except Exception: pass
        if self.min_mesh_size_mm > 0:
            try:
                sim.mesher.set_min_meshsize(self.min_mesh_size_mm * 1e-3)
            except Exception: pass

        # Bypass sim.generate_mesh() — its internal _configure_mesh_size() calls
        # setSizeFromBoundary=0 on every surface, then sets a coarse background
        # field.  This makes GMSH attempt to place 0.3 mm triangles on the 11 µm
        # OCC sliver surface 213, causing fatal edge-recovery failure even with
        # MeshAdapt.  The test_gerber_import.py script calls gmsh.model.mesh.generate(3)
        # directly (no _configure_mesh_size) and works correctly.
        #
        # We replicate what sim.generate_mesh() does, but:
        #   • skip _configure_mesh_size (let GMSH use set_curved_boundary_meshing)
        #   • call gmsh.model.mesh.generate(3) directly with Mesh.Algorithm=1
        #   • manually update sim.mesh state so run_sweep() proceeds
        import gmsh as _gmsh

        # Scan all surfaces to find the shortest boundary curve on any large
        # surface (>1 mm wide), then derive CharacteristicLengthMax from it.
        # Parameters come from [mesh] in emerge_config.toml.
        _ARTIFACT_THRESHOLD = self.artifact_threshold_um * 1e-6
        _cl_floor  = self.char_length_max_floor_mm * 1e-3
        _cl_ceil   = self.char_length_max_ceil_mm  * 1e-3
        _cl_factor = self.char_length_max_factor
        _min_tiny_len = 1.0  # m
        _n_skipped_artifact = 0
        _n_large_surfaces_scanned = 0
        _n_boundary_curves_scanned = 0
        _n_boundary_curves_used = 0
        try:
            # NOTE: do NOT call occ.synchronize() here.
            for _, _stag in _gmsh.model.getEntities(2):
                _sx = _gmsh.model.getBoundingBox(2, _stag)
                if (_sx[3]-_sx[0]) < 1e-3 or (_sx[4]-_sx[1]) < 1e-3:
                    continue
                _n_large_surfaces_scanned += 1
                for _, _ct in _gmsh.model.getBoundary(
                        [(2, _stag)], oriented=False):
                    _n_boundary_curves_scanned += 1
                    _cbb = _gmsh.model.getBoundingBox(1, abs(_ct))
                    _clen = ((_cbb[3]-_cbb[0])**2 +
                             (_cbb[4]-_cbb[1])**2 +
                             (_cbb[5]-_cbb[2])**2) ** 0.5
                    if _clen < _ARTIFACT_THRESHOLD:
                        _n_skipped_artifact += 1
                        continue
                    _n_boundary_curves_used += 1
                    if _clen < _min_tiny_len:
                        _min_tiny_len = _clen
        except Exception as _se:
            self._log(f"  Tiny-curve scan warning: {_se}")
        self._log(
            f"  CL scan coverage: large_surfaces={_n_large_surfaces_scanned}  "
            f"boundary_curves={_n_boundary_curves_scanned}  used={_n_boundary_curves_used}"
        )
        if _n_skipped_artifact:
            self._log(f"  Skipped {_n_skipped_artifact} artifact curves "
                      f"< {_ARTIFACT_THRESHOLD*1e6:.0f} µm in mesh size scan")
        _clmax = max(_cl_floor, min(_cl_ceil, _min_tiny_len * _cl_factor))
        self._log(f"  Shortest large-surface curve: {_min_tiny_len*1e3:.4f} mm  "
                  f"→ CharacteristicLengthMax = {_clmax*1e3:.4f} mm")

        for _o, _v in [("Mesh.Algorithm",              self.algorithm_2d),
                       ("Mesh.Algorithm3D",             self.algorithm_3d),
                       ("Mesh.Smoothing",               self.smoothing),
                       ("Mesh.CharacteristicLengthMax", _clmax),
                       ("Mesh.MaxNumThreads1D",         0),   # 0 = all cores
                       ("Mesh.MaxNumThreads2D",         0),
                       ("Mesh.MaxNumThreads3D",         0),
                       ]:
            try:
                _gmsh.option.setNumber(_o, _v)
                self._log(f"  GMSH option {_o} = {_v}")
            except Exception as _e:
                self._log(f"  GMSH option {_o} set failed: {_e}")

        # Flush deferred OCC computations.  Must come BEFORE apply_region_mesh_sizes
        # because occ.synchronize() after mesh.setSize() triggers an OCC crash.
        _gmsh.model.occ.synchronize()

        # Per-region mesh sizes (copper / component / substrate / air).
        if _GERBER_BUILDER_OK:
            apply_region_mesh_sizes(
                copper_mm    = self.mesh_copper_mm,
                copper_z_mm  = self.mesh_copper_z_mm,
                component_mm = self.mesh_component_mm,
                substrate_mm = self.mesh_substrate_mm,
                air_mm       = self.mesh_air_mm,
            )

        # Helper: minimum nodes for a curve.
        # Open curves (have distinct start/end points) → 2 nodes is fine.
        # Closed curves (circle/ellipse: start == end) → 2 nodes puts both
        # nodes at the same point, creating a degenerate 0-length edge.
        # Use 4 nodes for closed curves so GMSH creates a diamond-shaped quad
        # that is meshable as two triangles.
        def _min_nodes_for_curve(_ctag: int) -> int:
            try:
                _bpts = _gmsh.model.getBoundary(
                    [(1, _ctag)], oriented=False, combined=False)
                return 2 if len(_bpts) > 0 else 4
            except Exception:
                return 2

        # Constrain each sliver boundary curve to minimum-node count.
        # With just 2-4 boundary nodes per curve, each ~7 µm sliver surface
        # has only 3-4 unique boundary nodes — trivially meshable.
        # setTransfiniteCurve is a HARD constraint that overrides background
        # mesh size.
        _n_sl_curves = 0
        _n_sl_surfs  = 0
        _sliver_m    = self.sliver_threshold_mm * 1e-3
        _sliver_constrained_tags: set[int] = set()   # tracks which surfaces got constraints
        try:
            for _, _stag in _gmsh.model.getEntities(2):
                _sx = _gmsh.model.getBoundingBox(2, _stag)
                _dz_sl = abs(_sx[5] - _sx[2])
                if (_sx[3] - _sx[0]) < _sliver_m and (_sx[4] - _sx[1]) < _sliver_m:
                    _bcs = _gmsh.model.getBoundary([(2, _stag)], oriented=False)
                    for _, _ctag in _bcs:
                        try:
                            _gmsh.model.mesh.setTransfiniteCurve(
                                abs(_ctag), _min_nodes_for_curve(abs(_ctag)))
                            _n_sl_curves += 1
                        except Exception:
                            pass
                    _sliver_constrained_tags.add(_stag)
                    _n_sl_surfs += 1
            self._log(f"  Sliver curves constrained: "
                      f"{_n_sl_curves} curves on {_n_sl_surfs} surfaces")
        except Exception as _te:
            self._log(f"  Sliver curve setup warning: {_te}")

        # ── Pre-mesh: remove Discrete ghost surfaces ──────────────────────────
        # Gerber-derived Discrete surfaces with no adjacent volumes and Z < 10 µm
        # are boolean-op artefacts.  TetGen / Frontal-Delaunay cannot recover their
        # edges as PLC constraints because the zero-thickness polygon is coplanar
        # with adjacent volume faces.  Removing them before meshing prevents the
        # failure without switching to the much slower MeshAdapt algorithm.
        _ghost_pre_tags: list[int] = []
        try:
            for _, _gptag in list(_gmsh.model.getEntities(2)):
                try:
                    _gpbb  = _gmsh.model.getBoundingBox(2, _gptag)
                    _gpz   = abs(_gpbb[5] - _gpbb[2])
                    _gpvol = _gmsh.model.getAdjacencies(2, _gptag)[0]
                    _gptyp = _gmsh.model.getType(2, _gptag)
                    if (_gpz < 10e-6 and len(_gpvol) == 0
                            and "Discrete" in str(_gptyp)):
                        try:
                            _gmsh.model.removeEntities([(2, _gptag)], recursive=False)
                            _ghost_pre_tags.append(_gptag)
                        except Exception:
                            pass
                except Exception:
                    pass
            if _ghost_pre_tags:
                self._log(f"  Pre-mesh ghost removal: {len(_ghost_pre_tags)} "
                          f"Discrete orphan surface(s) removed: "
                          f"{_ghost_pre_tags[:20]}"
                          + (" …" if len(_ghost_pre_tags) > 20 else ""))
            else:
                self._log("  Pre-mesh ghost removal: none found")
        except Exception as _gpex:
            self._log(f"  Pre-mesh ghost scan warning: {_gpex}")

        # Use sim.generate_mesh() so _configure_mesh_size() runs — its background
        # Min field limits mesh size on arc pads.  If edge recovery fails on a
        # surface, constrain that surface's boundary curves to minimum node count
        # and retry (up to _MAX_MESH_RETRIES times).
        #
        # IMPORTANT: mesh.clear() erases transfinite prescriptions.  We therefore
        # accumulate all constraints in _extra_constraints and re-apply them after
        # every mesh.clear(), before calling sim.generate_mesh() again.
        import re as _re
        _MAX_MESH_RETRIES = self.max_mesh_retries
        _fixed_surfs: set[int] = set()
        _compounded_pairs: set[tuple[int, int]] = set()
        # list of (ctag, n) pairs to re-apply after each mesh.clear()
        _extra_constraints: list[tuple[int, int]] = []
        _meshadapt_used  = False   # Mesh.Algorithm → 1 (MeshAdapt)
        _hxt_fallback_used = False # Mesh.Algorithm3D → 10 (HXT)
        _oom_backoff_used = False
        _hxt_recovery_steps = 0

        self._log("Generating mesh …")
        _t_mesh = time.monotonic()
        _mesh_ok = False

        def _log_pre_mesh_diagnostics():
            """Emit mesh-complexity diagnostics before mesh generation starts."""
            try:
                x0, y0, z0, x1, y1, z1 = _gmsh.model.getBoundingBox(-1, -1)
                self._log(
                    "  Model bbox: "
                    f"({x0*1e3:.2f}, {y0*1e3:.2f}, {z0*1e3:.2f}) – "
                    f"({x1*1e3:.2f}, {y1*1e3:.2f}, {z1*1e3:.2f}) mm  "
                    f"size {(x1-x0)*1e3:.2f} x {(y1-y0)*1e3:.2f} x {(z1-z0)*1e3:.2f} mm"
                )
            except Exception:
                pass

            try:
                n_pts   = len(_gmsh.model.getEntities(0))
                n_cur   = len(_gmsh.model.getEntities(1))
                n_surf  = len(_gmsh.model.getEntities(2))
                n_vol   = len(_gmsh.model.getEntities(3))
                self._log(f"  Entities: points={n_pts}  curves={n_cur}  surfaces={n_surf}  volumes={n_vol}")
            except Exception:
                pass

            try:
                orphan_surfs = 0
                discrete_surfs = 0
                for _, stag in _gmsh.model.getEntities(2):
                    try:
                        if "Discrete" in str(_gmsh.model.getType(2, stag)):
                            discrete_surfs += 1
                    except Exception:
                        pass
                    try:
                        if len(_gmsh.model.getAdjacencies(2, stag)[0]) == 0:
                            orphan_surfs += 1
                    except Exception:
                        pass
                self._log(f"  Surface diagnostics: discrete={discrete_surfs}  orphan(no-volume)={orphan_surfs}")
            except Exception:
                pass

            try:
                _bg_fields = _gmsh.model.mesh.field.list()
                self._log(f"  Mesh fields configured: {len(_bg_fields)}")
            except Exception:
                pass

        def _log_post_mesh_diagnostics():
            """Emit mesh-size diagnostics after successful mesh generation."""
            try:
                _node_tags, _node_xyz, _node_params = _gmsh.model.mesh.getNodes()
                n_nodes = len(_node_tags)
                self._log(f"  Mesh nodes: {n_nodes}")
            except Exception:
                pass

            try:
                et2, etags2, enodes2 = _gmsh.model.mesh.getElements(2)
                n_el2 = sum(len(tags) for tags in etags2)
                et3, etags3, enodes3 = _gmsh.model.mesh.getElements(3)
                n_el3 = sum(len(tags) for tags in etags3)
                self._log(f"  Mesh elements: 2D={n_el2}  3D={n_el3}")
            except Exception:
                pass

        def _log_mesh_focus_diagnostics(max_surfaces: int = 8):
            """Report surfaces likely to dominate meshing effort."""
            try:
                _focus = []
                _tiny_total = 0
                for _, _stag in _gmsh.model.getEntities(2):
                    try:
                        _bcs = _gmsh.model.getBoundary([ (2, _stag) ], oriented=False)
                    except Exception:
                        continue
                    try:
                        _sbb = _gmsh.model.getBoundingBox(2, _stag)
                    except Exception:
                        _sbb = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    try:
                        _stype = str(_gmsh.model.getType(2, _stag))
                    except Exception:
                        _stype = "?"
                    try:
                        _adj_vols = _gmsh.model.getAdjacencies(2, _stag)[0]
                        _adj_vol_count = len(_adj_vols)
                    except Exception:
                        _adj_vol_count = -1
                    _n_bc = len(_bcs)
                    _min_bc_len = float("inf")
                    _tiny_here = 0
                    for _, _ct in _bcs:
                        try:
                            _cbb = _gmsh.model.getBoundingBox(1, abs(_ct))
                            _clen = ((_cbb[3]-_cbb[0])**2 + (_cbb[4]-_cbb[1])**2 + (_cbb[5]-_cbb[2])**2) ** 0.5
                            if _clen < _ARTIFACT_THRESHOLD:
                                _tiny_here += 1
                            if _clen < _min_bc_len:
                                _min_bc_len = _clen
                        except Exception:
                            pass
                    if _min_bc_len == float("inf"):
                        _min_bc_len = 0.0
                    _tiny_total += _tiny_here
                    _focus.append((
                        _n_bc,
                        -_tiny_here,
                        _min_bc_len,
                        _stag,
                        _tiny_here,
                        _sbb,
                        _stype,
                        _adj_vol_count,
                    ))

                _focus.sort(reverse=True)
                self._log(
                    f"  Mesh focus: top {min(max_surfaces, len(_focus))} surfaces by boundary complexity"
                )
                for _n_bc, _neg_tiny, _min_bc_len, _stag, _tiny_here, _sbb, _stype, _adj_vol_count in _focus[:max_surfaces]:
                    _sx = (_sbb[3] - _sbb[0]) * 1e3
                    _sy = (_sbb[4] - _sbb[1]) * 1e3
                    _sz = (_sbb[5] - _sbb[2]) * 1e3
                    _zc = ((_sbb[2] + _sbb[5]) * 0.5) * 1e3
                    self._log(
                        f"    surf {_stag}: boundary_curves={_n_bc}  "
                        f"tiny(<{_ARTIFACT_THRESHOLD*1e6:.0f}µm)={_tiny_here}  "
                        f"min_curve={_min_bc_len*1e3:.4f} mm  "
                        f"bbox={_sx:.2f}x{_sy:.2f}x{_sz:.4f} mm  zc={_zc:.3f} mm  "
                        f"adj_vols={_adj_vol_count}  type={_stype}"
                    )
                self._log(
                    f"  Mesh focus summary: surfaces={len(_focus)}  "
                    f"tiny_boundary_curves_total={_tiny_total}"
                )
            except Exception as _mfd_exc:
                self._log(f"  Mesh focus diagnostics warning: {_mfd_exc}")

        _log_pre_mesh_diagnostics()
        _log_mesh_focus_diagnostics()

        # ── Heartbeat helper — runs in a daemon thread during each mesh attempt ──
        import threading as _threading

        def _mesh_heartbeat(stop_evt: _threading.Event, log_fn, t0: float,
                            attempt_idx: int, interval: float = 30.0):
            """Print elapsed time + latest GMSH log line every `interval` seconds."""
            try:
                _gmsh.logger.start()
            except Exception:
                pass
            _last_msg = ""
            _seen_msgs = 0
            while not stop_evt.wait(timeout=interval):
                elapsed = time.monotonic() - t0
                # Grab the most recent GMSH internal log message (if any)
                try:
                    msgs = _gmsh.logger.get()
                    _clean = [m.strip() for m in msgs if str(m).strip()]
                    if _clean:
                        _seen_msgs += len(_clean)
                        _last_msg = _clean[-1]
                except Exception:
                    pass
                if _last_msg:
                    log_fn(
                        f"  [mesh a{attempt_idx}] still running … {elapsed:.0f} s  "
                        f"| gmsh_msgs={_seen_msgs}  | {_last_msg[:120]}"
                    )
                else:
                    log_fn(f"  [mesh a{attempt_idx}] still running … {elapsed:.0f} s")
            try:
                _gmsh.logger.stop()
            except Exception:
                pass

        for _attempt in range(_MAX_MESH_RETRIES + 1):
            _attempt_t0 = time.monotonic()
            self._log(f"  Mesh attempt {_attempt+1}/{_MAX_MESH_RETRIES+1}")
            # Re-apply any accumulated surface-fix constraints after clear
            _gmsh.model.mesh.clear()
            try:
                _entity_counts = tuple(
                    len(_gmsh.model.getEntities(_dim)) for _dim in (0, 1, 2, 3))
                _cl_attempt = _gmsh.option.getNumber("Mesh.CharacteristicLengthMax")
            except Exception:
                _entity_counts = (0, 0, 0, 0)
                _cl_attempt = float("nan")
            self._log(
                f"  Mesh attempt parameters: entities="
                f"points={_entity_counts[0]} curves={_entity_counts[1]} "
                f"surfaces={_entity_counts[2]} volumes={_entity_counts[3]} "
                f"CLmax={_cl_attempt*1e3:.4f} mm "
                f"curved_boundary={self.curved_boundary_resolution} "
                f"extra_constraints={len(_extra_constraints)}")
            for _ec_tag, _ec_n in _extra_constraints:
                try:
                    _gmsh.model.mesh.setTransfiniteCurve(_ec_tag, _ec_n)
                except Exception:
                    pass
            _stop_hb = _threading.Event()
            _hb_thread = _threading.Thread(
                target=_mesh_heartbeat,
                args=(_stop_hb, self._log, _t_mesh, _attempt + 1),
                daemon=True,
            )
            _hb_thread.start()
            try:
                sim.generate_mesh()
                _stop_hb.set()
                _hb_thread.join(timeout=2)
                self._log(
                    f"  Mesh done. total={time.monotonic()-_t_mesh:.1f} s  "
                    f"attempt={time.monotonic()-_attempt_t0:.1f} s"
                )
                _log_post_mesh_diagnostics()
                _mesh_ok = True
                break
            except Exception as _mesh_exc:
                _stop_hb.set()
                _hb_thread.join(timeout=2)
                _mesh_msg = str(_mesh_exc)
                self._log(
                    f"  Mesh attempt {_attempt+1} failed after "
                    f"{time.monotonic()-_attempt_t0:.1f} s"
                )
                self._log(
                    f"  Mesh exception: {type(_mesh_exc).__name__}: "
                    f"{_mesh_msg or repr(_mesh_exc)}")
                self._log("  Mesh traceback:\n" + traceback.format_exc())
                try:
                    _after_counts = tuple(
                        len(_gmsh.model.getEntities(_dim)) for _dim in (0, 1, 2, 3))
                    _nodes, _, _ = _gmsh.model.mesh.getNodes()
                    self._log(
                        f"  Mesh state after failure: entities="
                        f"points={_after_counts[0]} curves={_after_counts[1]} "
                        f"surfaces={_after_counts[2]} volumes={_after_counts[3]} "
                        f"nodes={len(_nodes)}")
                except Exception as _mesh_diag_exc:
                    self._log(f"  Mesh state diagnostics failed: {_mesh_diag_exc}")

                _is_oom = (
                    "Unable to allocate" in _mesh_msg
                    or "bad_alloc" in _mesh_msg
                    or "MemoryError" in _mesh_msg
                    or "Out of memory" in _mesh_msg
                )
                if _is_oom:
                    self._log("  Mesh memory exhaustion detected.")
                    try:
                        _n_pts = len(_gmsh.model.getEntities(0))
                        _n_cur = len(_gmsh.model.getEntities(1))
                        _n_sur = len(_gmsh.model.getEntities(2))
                        _n_vol = len(_gmsh.model.getEntities(3))
                        self._log(
                            f"  Geometry at failure: points={_n_pts}  curves={_n_cur}  "
                            f"surfaces={_n_sur}  volumes={_n_vol}"
                        )
                    except Exception:
                        pass

                    if not _oom_backoff_used:
                        _oom_backoff_used = True
                        _oom_scale = 1.8
                        self._log(
                            "  Retrying once with coarse OOM backoff "
                            f"(region sizes x{_oom_scale:.1f}, CharacteristicLengthMax x2)."
                        )
                        try:
                            _oom_clmax = _gmsh.option.getNumber("Mesh.CharacteristicLengthMax")
                            _oom_clmax_new = min(_cl_ceil, max(_cl_floor, _oom_clmax * 2.0))
                            _gmsh.option.setNumber("Mesh.CharacteristicLengthMax", _oom_clmax_new)
                            self._log(
                                "    OOM backoff: Mesh.CharacteristicLengthMax "
                                f"{_oom_clmax*1e3:.4f} → {_oom_clmax_new*1e3:.4f} mm"
                            )
                        except Exception as _oom_opt_exc:
                            self._log(f"    OOM backoff warning (CharacteristicLengthMax): {_oom_opt_exc}")

                        if _GERBER_BUILDER_OK:
                            try:
                                apply_region_mesh_sizes(
                                    copper_mm    = self.mesh_copper_mm * _oom_scale,
                                    copper_z_mm  = self.mesh_copper_z_mm * _oom_scale,
                                    component_mm = self.mesh_component_mm * _oom_scale,
                                    substrate_mm = self.mesh_substrate_mm * _oom_scale,
                                    air_mm       = self.mesh_air_mm * _oom_scale,
                                )
                                self._log(
                                    "    OOM backoff regions: "
                                    f"Cu={self.mesh_copper_mm*_oom_scale:.2f} "
                                    f"Comp={self.mesh_component_mm*_oom_scale:.2f} "
                                    f"Sub={self.mesh_substrate_mm*_oom_scale:.2f} "
                                    f"Air={self.mesh_air_mm*_oom_scale:.2f} mm"
                                )
                            except Exception as _oom_reg_exc:
                                self._log(f"    OOM backoff warning (region sizing): {_oom_reg_exc}")
                        continue

                    self._log("  Mesh OOM persisted after coarse retry.")
                    self._log("  Suggested [mesh] TOML adjustments for this design:")
                    self._log(f"    cells_per_lambda = {max(6, min(self.cells_per_lambda, 6))}")
                    self._log(f"    mesh_copper_mm = {max(self.mesh_copper_mm, 0.20):.2f}")
                    self._log(f"    mesh_copper_z_mm = {max(self.mesh_copper_z_mm, 0.16):.2f}")
                    self._log(f"    mesh_component_mm = {max(self.mesh_component_mm, 0.30):.2f}")
                    self._log(f"    mesh_substrate_mm = {max(self.mesh_substrate_mm, 0.80):.2f}")
                    self._log(f"    mesh_air_mm = {max(self.mesh_air_mm, 6.0):.1f}")
                    self._log(f"    char_length_max_factor = {max(self.char_length_max_factor, 3.5):.1f}")
                    self._log(f"    artifact_threshold_um = {max(self.artifact_threshold_um, 150.0):.0f}")
                    self._log(f"  generate_mesh() raised: {_mesh_exc}")
                    raise

                # ── Overlapping facets: compound the two conflicting surfaces ──
                if "overlapping facets" in _mesh_msg or "Invalid boundary mesh" in _mesh_msg:
                    _sm2 = _re.findall(r'surface (\d+)', _mesh_msg)
                    if len(_sm2) >= 2:
                        _sa, _sb = int(_sm2[0]), int(_sm2[1])
                        _pair = (min(_sa, _sb), max(_sa, _sb))
                        if _pair not in _compounded_pairs:
                            _compounded_pairs.add(_pair)
                            try:
                                _gmsh.model.mesh.setCompound(2, [_sa, _sb])
                                self._log(f"  Overlapping facets surfaces {_sa},{_sb} "
                                          f"→ setCompound (attempt {_attempt+1})")
                            except Exception as _ce:
                                self._log(f"  setCompound failed: {_ce}")
                            continue
                    # If we can't compound (only one surface tag or already tried),
                    # fall back to HXT which doesn't use TetGen
                    if not _hxt_fallback_used:
                        _hxt_fallback_used = True
                        try:
                            _gmsh.option.setNumber("Mesh.Algorithm3D", 10)
                            self._log(f"  Switching to HXT (Algorithm3D=10) after "
                                      f"overlapping-facets failure")
                        except Exception:
                            pass
                        continue
                    self._log(f"  generate_mesh() raised: {_mesh_exc}")
                    raise

                # ── OCC virtual entity (high tag after setCompound): go to HXT ──
                if "Unknown OpenCASCADE entity" in _mesh_msg:
                    _unknown_fixed = False
                    _um = _re.search(r"dimension\s+(\d+)\s+with\s+tag\s+(\d+)", _mesh_msg)
                    if _um and _attempt < _MAX_MESH_RETRIES:
                        _udim = int(_um.group(1))
                        _utag = int(_um.group(2))
                        try:
                            if _udim == 2:
                                _adj = _gmsh.model.getAdjacencies(2, _utag)[0]
                                if len(_adj) == 0:
                                    _gmsh.model.removeEntities([(2, _utag)], recursive=False)
                                    self._log(f"  Removed orphan OCC surface {_utag} after unknown-entity error — retrying")
                                    _unknown_fixed = True
                        except Exception as _uexc:
                            self._log(f"  Unknown-entity cleanup warning: {_uexc}")

                    if _unknown_fixed:
                        continue

                    if not _hxt_fallback_used:
                        _hxt_fallback_used = True
                        try:
                            _gmsh.option.setNumber("Mesh.Algorithm3D", 10)
                            self._log(f"  OCC entity error after setCompound "
                                      f"→ switching to HXT (Algorithm3D=10)")
                        except Exception:
                            pass
                        continue
                    self._log(f"  generate_mesh() raised: {_mesh_exc}")
                    raise

                # Generic HXT failures can hide an edge-recovery root cause.
                # Apply automatic conservative tuning and retry before aborting.
                _is_generic_hxt_fail = (
                    "HXT 3D mesh failed" in _mesh_msg
                    or "GMSH Mesh error detected" in _mesh_msg
                )
                if _is_generic_hxt_fail and _attempt < _MAX_MESH_RETRIES and _hxt_recovery_steps < 2:
                    _hxt_recovery_steps += 1
                    try:
                        _cl_cur = _gmsh.option.getNumber("Mesh.CharacteristicLengthMax")
                    except Exception:
                        _cl_cur = _clmax

                    # Recovery must be able to tighten CLmax even when the user
                    # has floor==ceil (speed profile). Use an absolute lower
                    # bound only for recovery attempts.
                    _cl_abs_min = 0.05e-3  # 0.05 mm
                    _cl_new = max(_cl_abs_min, _cl_cur * 0.65)
                    try:
                        _gmsh.option.setNumber("Mesh.CharacteristicLengthMax", _cl_new)
                    except Exception:
                        pass

                    self._log(
                        f"  Generic HXT recovery step {_hxt_recovery_steps}: "
                        f"CLmax {_cl_cur*1e3:.4f} -> {_cl_new*1e3:.4f} mm, "
                        f"curved_boundary unchanged at {self.curved_boundary_resolution}"
                    )
                    continue

                _is_edge_recovery  = "Unable to recover the edge" in _mesh_msg
                _is_wrong_topology = "Wrong topology of boundary mesh" in _mesh_msg
                if not (_is_edge_recovery or _is_wrong_topology):
                    self._log(f"  generate_mesh() raised: {_mesh_exc}")
                    raise
                # ── "Wrong topology" fast path ─────────────────────────────
                # OCC parametrisation failure: surface has degenerate/non-manifold
                # boundary loops.  If the surface has no adjacent volumes it is safe
                # to remove it (orphaned sliver or Discrete artefact).
                if _is_wrong_topology:
                    _wt_sm = _re.search(r'surface (\d+)', _mesh_msg)
                    _wt_tag = int(_wt_sm.group(1)) if _wt_sm else None
                    if _wt_tag is not None:
                        try:
                            _wt_vol = _gmsh.model.getAdjacencies(2, _wt_tag)[0]
                            if len(_wt_vol) == 0:
                                _gmsh.model.removeEntities([(2, _wt_tag)], recursive=False)
                                self._log(f"  Wrong-topology orphan surface {_wt_tag} removed — retrying")
                                continue
                            else:
                                self._log(
                                    f"  Wrong-topology surface {_wt_tag} has "
                                    f"{len(_wt_vol)} adjacent volume(s) — cannot remove")
                        except Exception as _wte:
                            self._log(f"  Wrong-topology removal failed ({_wte})")
                    else:
                        self._log(f"  Wrong-topology error (no surface tag): {_mesh_msg[:200]}")
                    self._log(f"  generate_mesh() raised: {_mesh_exc}")
                    raise
                # ── "Unable to recover the edge" path ─────────────────────
                # Parse the failing surface tag from the error message
                _sm = _re.search(r'on surface (\d+)', _mesh_msg)
                if not _sm:
                    self._log(f"  Edge recovery error (can't parse surface): {_mesh_msg}")
                    break
                _fail_surf = int(_sm.group(1))
                # On first encounter: report why this surface wasn't pre-filtered.
                if _fail_surf not in _fixed_surfs:
                    try:
                        _fsbb  = _gmsh.model.getBoundingBox(2, _fail_surf)
                        _fsdx  = abs(_fsbb[3]-_fsbb[0])*1e3
                        _fsdy  = abs(_fsbb[4]-_fsbb[1])*1e3
                        _fsdz  = abs(_fsbb[5]-_fsbb[2])*1e6   # µm
                        _fsvol = _gmsh.model.getAdjacencies(2, _fail_surf)[0]
                        _fstyp = _gmsh.model.getType(2, _fail_surf)
                        _in_sliver = _fail_surf in _sliver_constrained_tags
                        self._log(
                            f"  Surface {_fail_surf} bbox: "
                            f"({_fsbb[0]*1e3:.3f}, {_fsbb[1]*1e3:.3f}, {_fsbb[2]*1e3:.3f}) – "
                            f"({_fsbb[3]*1e3:.3f}, {_fsbb[4]*1e3:.3f}, {_fsbb[5]*1e3:.3f}) mm  "
                            f"size {_fsdx:.4f} x {_fsdy:.4f} x 0.{_fsdz:.4f} mm  "
                            + ("*** ZERO-THICKNESS (coplanar) ***" if _fsdz < 1e-3 else f"dz={_fsdz:.2f} µm"))
                        self._log(
                            f"  Surface {_fail_surf} geometry type: {_fstyp!r}")
                        self._log(
                            f"  Surface {_fail_surf} adjacent volumes: {list(_fsvol)}"
                            f"  adjacent surfaces (upward): "
                            f"{list(_gmsh.model.getAdjacencies(2, _fail_surf)[1])[:20]}")
                        self._log(
                            f"  Surface {_fail_surf} pre-filter verdict: "
                            f"sliver_transfinite={_in_sliver}  "
                            f"no_volumes={len(_fsvol)==0}  "
                            f"dz_um={_fsdz:.2f}  "
                            f"xy_mm={max(_fsdx,_fsdy):.4f}  "
                            f"threshold_mm={self.sliver_threshold_mm:.2f}  "
                            f"ghost_z_limit_um=10.0")
                        _fsbcs = _gmsh.model.getBoundary([(2, _fail_surf)], oriented=False)
                        self._log(f"  Boundary curves ({len(_fsbcs)}):")
                        for _, _fcc in _fsbcs:
                            _fccbb = _gmsh.model.getBoundingBox(1, abs(_fcc))
                            _fccdiag = ((_fccbb[3]-_fccbb[0])**2+(_fccbb[4]-_fccbb[1])**2+(_fccbb[5]-_fccbb[2])**2)**0.5
                            _fcctyp  = _gmsh.model.getType(1, abs(_fcc))
                            _fccpts  = _gmsh.model.getBoundary([(1, abs(_fcc))], oriented=False, combined=False)
                            self._log(
                                f"    curve {abs(_fcc)}: type={_fcctyp!r}  "
                                f"bbox_diag={_fccdiag*1e3:.4f} mm  "
                                f"Z=[{_fccbb[2]*1e3:.4f},{_fccbb[5]*1e3:.4f}] mm  "
                                f"span={abs(_fccbb[5]-_fccbb[2])*1e3:.4f} mm  "
                                f"endpoints={len(_fccpts)}")
                    except Exception as _fde:
                        self._log(f"  [DEBUG] surface {_fail_surf} diagnosis failed: {_fde}")

                if _fail_surf in _fixed_surfs:
                    # ── Pre-escalation: remove ghost faces (no volume, Z=0) ───
                    # A 'Discrete surface' with no adjacent volumes and zero
                    # Z-thickness is an OCC boolean artefact that cannot be
                    # meshed and is not part of any solid.  TetGen still tries
                    # to recover its edges as PLC constraints and always fails.
                    # Removing it lets TetGen proceed normally.
                    _ghost_removed = False
                    try:
                        _ghbb  = _gmsh.model.getBoundingBox(2, _fail_surf)
                        _ghz   = abs(_ghbb[5] - _ghbb[2])          # Z thickness (m)
                        _ghvol = _gmsh.model.getAdjacencies(2, _fail_surf)[0]
                        _ghtyp = _gmsh.model.getType(2, _fail_surf)
                        if (_ghz < 10e-6 and len(_ghvol) == 0
                                and "Discrete" in str(_ghtyp)):
                            # Near-ghost face: Discrete surface, no volumes, Z < 10 µm.
                            # Strategy depends on curve count:
                            #   ≤4 curves → setTransfiniteSurface (trivial structured mesh)
                            #   >4 curves → setCompound with largest adjacent surface so GMSH
                            #               parametrises it jointly (transfinite requires 3-4 corners)
                            try:
                                _ghbcs = _gmsh.model.getBoundary(
                                    [(2, _fail_surf)], oriented=False)
                                _n_gh_curves = len(_ghbcs)
                                # Discrete surfaces have no CAD parametrisation —
                                # setCompound and setTransfiniteSurface require it.
                                # Per-curve transfinite only; MeshAdapt escalation
                                # handles the surface meshing on next retry.
                                _gh_is_discrete = "Discrete" in str(_ghtyp)
                                for _, _ghc in _ghbcs:
                                    _gmsh.model.mesh.setTransfiniteCurve(abs(_ghc), 2)
                                if _gh_is_discrete:
                                    _strategy = (f"per-curve transfinite only "
                                                 f"({_n_gh_curves} curves, Discrete — "
                                                 f"no setCompound/setTransfinite)")
                                elif _n_gh_curves in (3, 4):
                                    _gmsh.model.mesh.setTransfiniteSurface(_fail_surf)
                                    _strategy = f"transfinite ({_n_gh_curves} curves)"
                                else:
                                    # CAD surface with >4 curves: setCompound with
                                    # largest adjacent non-Discrete surface
                                    _gh_curve_set = {abs(c[1]) for c in _ghbcs}
                                    _gh_adj_surfs: list[tuple[int, float]] = []
                                    for _, _as in _gmsh.model.getEntities(2):
                                        if _as == _fail_surf:
                                            continue
                                        try:
                                            _as_type = _gmsh.model.getType(2, _as)
                                        except Exception:
                                            _as_type = ""
                                        if "Discrete" in str(_as_type):
                                            continue
                                        _as_bcs = {abs(c[1]) for c in
                                                   _gmsh.model.getBoundary([(2, _as)], oriented=False)}
                                        if _gh_curve_set & _as_bcs:
                                            _asbb = _gmsh.model.getBoundingBox(2, _as)
                                            _as_area = ((_asbb[3]-_asbb[0]) *
                                                        (_asbb[4]-_asbb[1]))
                                            _gh_adj_surfs.append((_as, _as_area))
                                    if _gh_adj_surfs:
                                        _gh_anchor = max(_gh_adj_surfs, key=lambda x: x[1])[0]
                                        _gmsh.model.mesh.setCompound(2, [_gh_anchor, _fail_surf])
                                        _strategy = (f"setCompound with anchor {_gh_anchor} "
                                                     f"({_n_gh_curves} curves)")
                                    else:
                                        _strategy = f"per-curve transfinite fallback ({_n_gh_curves} curves)"
                            except Exception as _ghe2:
                                _strategy = f"failed: {_ghe2}"
                            self._log(
                                f"  Surface {_fail_surf}: near-ghost face (Discrete, "
                                f"Z={_ghz*1e6:.2f} µm < 10 µm, no volumes) — {_strategy}")
                            # For Discrete surfaces the pre-mesh scan should have
                            # removed this surface already.  If it's still here,
                            # try removeEntities now instead of switching global
                            # MeshAdapt (which is catastrophically slow on large
                            # copper planes and can loop for hours).
                            if _gh_is_discrete:
                                _disc_rm_ok = False
                                try:
                                    _gmsh.model.removeEntities(
                                        [(2, _fail_surf)], recursive=False)
                                    self._log(
                                        f"  Discrete ghost surface {_fail_surf} "
                                        f"removed mid-mesh — retrying")
                                    _disc_rm_ok = True
                                except Exception as _drme:
                                    self._log(
                                        f"  Discrete ghost removeEntities failed "
                                        f"({_drme}) — escalating to HXT")
                                if not _disc_rm_ok and not _hxt_fallback_used:
                                    _hxt_fallback_used = True
                                    try:
                                        _gmsh.option.setNumber("Mesh.Algorithm3D", 10)
                                        self._log(
                                            f"  Discrete ghost → HXT (Algorithm3D=10)")
                                    except Exception:
                                        pass
                            _ghost_removed = True
                            continue
                    except Exception as _ghe:
                        self._log(f"  [DEBUG] ghost-face check failed: {_ghe}")
                    if _ghost_removed:
                        continue
                    # ── Full state dump at every escalation attempt ───────────
                    self._log(f"  [DEBUG] GMSH error (raw): {_mesh_msg[:300]}")
                    try:
                        _esbb = _gmsh.model.getBoundingBox(2, _fail_surf)
                        _esx  = abs(_esbb[3]-_esbb[0])*1e3
                        _esy  = abs(_esbb[4]-_esbb[1])*1e3
                        _esz  = abs(_esbb[5]-_esbb[2])*1e3
                        _esZ0 = _esbb[2]*1e3   # absolute Z of the surface
                        _esc_count = len(_gmsh.model.getBoundary(
                            [(2, _fail_surf)], oriented=False))
                        _esalg2d  = int(_gmsh.option.getNumber("Mesh.Algorithm"))
                        _esalg3d  = int(_gmsh.option.getNumber("Mesh.Algorithm3D"))
                        _esclmax  = _gmsh.option.getNumber("Mesh.CharacteristicLengthMax")
                        self._log(
                            f"  [DEBUG] Surface {_fail_surf} escalation state:  "
                            f"bbox={_esx:.4f}×{_esy:.4f}×{_esz:.4f} mm  Z0={_esZ0:.4f} mm  "
                            f"curves={_esc_count}  "
                            f"Alg2D={_esalg2d}  Alg3D={_esalg3d}  CLmax={_esclmax*1e3:.4f} mm  "
                            f"meshadapt={_meshadapt_used}  hxt={_hxt_fallback_used}"
                            + ("  *** Z=0 coplanar ***" if _esz < 1e-6 else "")
                        )
                        # Geometry type of surface
                        try:
                            _estype = _gmsh.model.getType(2, _fail_surf)
                            self._log(f"  [DEBUG] Surface {_fail_surf} geometry type: {_estype!r}")
                        except Exception:
                            pass
                        # Parametric area (sanity check for degenerate face)
                        try:
                            _esarea = _gmsh.model.occ.getMass(2, _fail_surf)
                            self._log(f"  [DEBUG] Surface {_fail_surf} OCC area: {_esarea*1e6:.6f} mm²"
                                      + ("  *** DEGENERATE (near-zero area) ***" if _esarea < 1e-12 else ""))
                        except Exception:
                            pass
                        # Shared volumes
                        try:
                            _esvols = _gmsh.model.getAdjacencies(2, _fail_surf)
                            self._log(f"  [DEBUG] Surface {_fail_surf} in volumes: {list(_esvols[0])}")
                        except Exception:
                            pass
                        # Transfinite constraints currently active on curves of this surface
                        _esc_transfinite = []
                        for _, _ct in _gmsh.model.getBoundary([(2, _fail_surf)], oriented=False):
                            if (abs(_ct), ) in [(c,) for c, _ in _extra_constraints]:
                                _esc_transfinite.append(abs(_ct))
                        self._log(f"  [DEBUG] Surface {_fail_surf} curves with transfinite: "
                                  f"{len(_esc_transfinite)}/{_esc_count}  ids={_esc_transfinite[:10]}")
                        # Current node count in the mesh (did GMSH produce anything?)
                        try:
                            _es_nodes, _, _ = _gmsh.model.mesh.getNodes()
                            self._log(f"  [DEBUG] Current mesh node count: {len(_es_nodes)}")
                        except Exception:
                            pass
                    except Exception as _esde:
                        self._log(f"  [DEBUG] Surface {_fail_surf} escalation diagnostic failed: {_esde}")
                    # ── Escalation step 1: MeshAdapt (2D) + fine point sizes ──
                    # "Unable to recover edge" is a 2D CDT failure.  MeshAdapt
                    # (Algorithm=1) skips strict edge recovery and handles degenerate
                    # surface geometry much better than Frontal-Delaunay (6).
                    # Also force a very fine mesh at the surface's boundary points.
                    if not _meshadapt_used:
                        _meshadapt_used = True
                        try:
                            _gmsh.option.setNumber("Mesh.Algorithm", 1)
                            self._log(f"  Surface {_fail_surf} transfinite fix failed "
                                      f"→ switching 2D algorithm to MeshAdapt (1)")
                        except Exception as _exc:
                            self._log(f"  [DEBUG] setNumber Mesh.Algorithm=1 failed: {_exc}")
                        # Force sub-surface-size mesh at all boundary vertices
                        try:
                            _sbb = _gmsh.model.getBoundingBox(2, _fail_surf)
                            _smin = min(abs(_sbb[3]-_sbb[0]),
                                        abs(_sbb[4]-_sbb[1]),
                                        abs(_sbb[5]-_sbb[2]))
                            if _smin < 1e-9:
                                _smin = max(abs(_sbb[3]-_sbb[0]),
                                            abs(_sbb[4]-_sbb[1]),
                                            abs(_sbb[5]-_sbb[2]))
                            _pt_sz = max(_smin / 4.0, 1e-5)
                            _bpts: set[int] = set()
                            for _, _sc in _gmsh.model.getBoundary(
                                    [(2, _fail_surf)], oriented=False):
                                for _, _sp in _gmsh.model.getBoundary(
                                        [(1, abs(_sc))], oriented=False):
                                    _bpts.add(abs(_sp))
                            self._log(
                                f"  [DEBUG] Surface {_fail_surf} smin={_smin*1e3:.5f} mm  "
                                f"pt_sz={_pt_sz*1e3:.5f} mm  boundary_pts={len(_bpts)}")
                            if _bpts:
                                _gmsh.model.mesh.setSize(
                                    [(0, _p) for _p in _bpts], _pt_sz)
                                self._log(
                                    f"    Set {len(_bpts)} boundary pts to "
                                    f"{_pt_sz*1e3:.4f} mm")
                            else:
                                self._log(f"  [DEBUG] Surface {_fail_surf}: no boundary points found — skipping setSize")
                        except Exception as _fse:
                            self._log(f"    Fine-point-size warning: {_fse}")
                        continue
                    # ── Escalation step 2: HXT (3D) ──
                    if not _hxt_fallback_used:
                        _hxt_fallback_used = True
                        try:
                            _gmsh.option.setNumber("Mesh.Algorithm3D", 10)
                            self._log(f"  Surface {_fail_surf} MeshAdapt still fails "
                                      f"→ switching to HXT (Algorithm3D=10)")
                        except Exception as _exc:
                            self._log(f"  [DEBUG] setNumber Mesh.Algorithm3D=10 failed: {_exc}")
                        continue
                    self._log(f"  Surface {_fail_surf} still fails after "
                              f"transfinite + MeshAdapt + HXT — stopping retries.")
                    break
                _fixed_surfs.add(_fail_surf)
                # ── Full diagnostics on first encounter ──────────────────────
                try:
                    _bb = _gmsh.model.getBoundingBox(2, _fail_surf)
                    _bbx = abs(_bb[3]-_bb[0])*1e3
                    _bby = abs(_bb[4]-_bb[1])*1e3
                    _bbz = abs(_bb[5]-_bb[2])*1e3
                    self._log(f"    Surface {_fail_surf} bbox: "
                              f"({_bb[0]*1e3:.3f}, {_bb[1]*1e3:.3f}, {_bb[2]*1e3:.3f}) – "
                              f"({_bb[3]*1e3:.3f}, {_bb[4]*1e3:.3f}, {_bb[5]*1e3:.3f}) mm  "
                              f"size {_bbx:.4f} x {_bby:.4f} x {_bbz:.4f} mm"
                              + ("  *** ZERO-THICKNESS (coplanar) ***" if _bbz < 1e-6 else ""))
                except Exception as _de:
                    self._log(f"    getBoundingBox({_fail_surf}) failed: {_de}")

                # Surface geometry type and area
                try:
                    _stype = _gmsh.model.getType(2, _fail_surf)
                    self._log(f"    Surface {_fail_surf} geometry type: {_stype!r}")
                except Exception:
                    pass
                try:
                    _mass = _gmsh.model.occ.getMass(2, _fail_surf)
                    self._log(f"    Surface {_fail_surf} area (OCC): {_mass*1e6:.4f} mm²"
                              + ("  *** NEAR-ZERO AREA ***" if _mass < 1e-12 else ""))
                except Exception:
                    try:
                        _mass = _gmsh.model.geo.getMass(2, _fail_surf)
                        self._log(f"    Surface {_fail_surf} area (geo): {_mass*1e6:.4f} mm²")
                    except Exception:
                        pass

                # Adjacent volumes (which solids share this face)
                try:
                    _adj_vols = _gmsh.model.getAdjacencies(2, _fail_surf)
                    self._log(f"    Surface {_fail_surf} adjacent volumes: {list(_adj_vols[0])}  "
                              f"adjacent surfaces (upward): {list(_adj_vols[1])}")
                except Exception:
                    pass

                # Whether surface is in a compound group
                try:
                    _all_compounds = _gmsh.model.mesh.getCompounds()
                    _in_compound = any(_fail_surf in grp for _, grp in _all_compounds
                                       if isinstance(grp, (list, tuple)))
                    self._log(f"    Surface {_fail_surf} in compound group: {_in_compound}")
                except Exception:
                    pass

                # Boundary curves — full detail
                try:
                    _bcs_diag = _gmsh.model.getBoundary(
                        [(2, _fail_surf)], oriented=False)
                    self._log(f"    Boundary curves ({len(_bcs_diag)}):")
                    for _, _dct in _bcs_diag[:30]:
                        _dct = abs(_dct)
                        _dcbb = _gmsh.model.getBoundingBox(1, _dct)
                        _dlen = ((_dcbb[3]-_dcbb[0])**2 +
                                 (_dcbb[4]-_dcbb[1])**2 +
                                 (_dcbb[5]-_dcbb[2])**2) ** 0.5
                        _dpts = _gmsh.model.getBoundary(
                            [(1, _dct)], oriented=False, combined=False)
                        try:
                            _ctype = _gmsh.model.getType(1, _dct)
                        except Exception:
                            _ctype = "?"
                        # Z range of this curve
                        _czmin = _dcbb[2]*1e3
                        _czmax = _dcbb[5]*1e3
                        _czspan = abs(_czmax - _czmin)
                        self._log(f"      curve {_dct}: type={_ctype!r}  "
                                  f"bbox_diag={_dlen*1e3:.4f} mm  "
                                  f"Z=[{_czmin:.4f},{_czmax:.4f}] mm  span={_czspan:.4f} mm  "
                                  f"endpoints={len(_dpts)}")
                    if len(_bcs_diag) > 30:
                        self._log(f"      ... ({len(_bcs_diag)-30} more curves)")
                except Exception as _de:
                    self._log(f"    Boundary curve diagnostics failed: {_de}")
                # Collect boundary curves of the failing surface and add them
                # to the persistent constraint list.
                # Open curves (have distinct endpoints) → 2 nodes.
                # Closed curves (circle: start==end, getBoundary returns []) → 4
                # nodes so GMSH creates a diamond rather than a degenerate point.
                _n_added = 0
                try:
                    for _, _ct in _gmsh.model.getBoundary(
                            [(2, _fail_surf)], oriented=False):
                        _cn = _min_nodes_for_curve(abs(_ct))
                        _extra_constraints.append((abs(_ct), _cn))
                        _n_added += 1
                except Exception as _be:
                    self._log(f"  getBoundary({_fail_surf}) failed: {_be}")
                self._log(f"  Attempt {_attempt+1}: surface {_fail_surf} edge "
                          f"recovery failed — added {_n_added} curve constraints, "
                          f"retrying …  ({time.monotonic()-_t_mesh:.1f} s)")
        if not _mesh_ok:
            self._log(f"  WARNING: mesh did not complete after "
                      f"{len(_fixed_surfs)} surface fixes "
                      f"({time.monotonic()-_t_mesh:.1f} s)")

        if self.show_mesh:
            self._log("Showing 3D mesh — close the viewer window to continue ...")
            mode = self.mesh_viewer
            allow_gmsh = mode in ("auto", "gmsh", "both")
            allow_emerge = mode in ("auto", "emerge", "native", "both")
            shown = False
            emerge_failed = False

            if allow_gmsh and _GERBER_BUILDER_OK:
                try:
                    gmsh_view_mesh("Mesh — close window to continue")
                    shown = True
                except Exception as exc:
                    self._log(f"WARNING: GMSH mesh viewer failed: {exc}")

            if allow_emerge and (mode != "auto" or not shown):
                try:
                    _sim_view(sim, plot_mesh=True, labels=False, bc=False,
                              use_gmsh=True)
                    shown = True
                except Exception as exc:
                    emerge_failed = True
                    self._log(f"WARNING: EMerge mesh viewer failed: {exc}")

            # Fallback: if EMerge viewer was selected and failed, try GMSH.
            if (not shown) and emerge_failed and _GERBER_BUILDER_OK:
                try:
                    self._log("  Falling back to GMSH mesh viewer ...")
                    gmsh_view_mesh("Mesh (fallback) — close window to continue")
                    shown = True
                except Exception as exc:
                    self._log(f"WARNING: GMSH mesh fallback failed: {exc}")

            if not shown:
                self._log("WARNING: no mesh viewer backend available")

        self._log("")
        self._log(_SEP)
        self._log("Stage 4 / 5 — Running FEM sweep")
        self._log(_SEP)
        self._log(f"  Frequency: {self.freq_start/1e6:.0f} MHz – "
                  f"{self.freq_stop/1e9:.1f} GHz  ({self.freq_steps} points)")

        _t_sweep = time.monotonic()
        self._log("Running FEM sweep …")

        # ── FEM heartbeat — mirrors mesh heartbeat; fires every 30 s ─────────
        import threading as _threading_fem
        import subprocess as _nvsmi

        def _fem_heartbeat(stop_evt, log_fn, t0, interval=30.0):
            while not stop_evt.wait(timeout=interval):
                elapsed = time.monotonic() - t0
                sys_info = _system_usage_snapshot()
                gpu_info = ""
                try:
                    _r = _nvsmi.run(
                        ["nvidia-smi",
                         "--query-gpu=utilization.gpu,memory.used,memory.total",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=3,
                    )
                    if _r.returncode == 0 and _r.stdout.strip():
                        _p = [x.strip() for x in _r.stdout.strip().split(",")]
                        if len(_p) >= 3:
                            gpu_info = f"  GPU {_p[0]}% util  {_p[1]}/{_p[2]} MiB"
                except Exception:
                    pass
                log_fn(f"  [FEM] still running … {elapsed:.0f} s  {sys_info}{gpu_info}")

        _stop_fem_hb = _threading_fem.Event()
        _fem_hb = _threading_fem.Thread(
            target=_fem_heartbeat,
            args=(_stop_fem_hb, self._log, _t_sweep),
            daemon=True,
        )
        _fem_hb.start()
        try:
            mw_data = sim.mw.run_sweep()
        except Exception as _sweep_err:
            _emsg = str(_sweep_err)
            # "Allocation failed" means the FEM matrix is too large for the
            # chosen solver.  Retry with PARDISO (CPU RAM) then SuperLU.
            if any(k in _emsg.lower() for k in ("allocation", "too large", "memory")):
                self._log(f"  WARNING: Solver failed ({_emsg.strip()}) — retrying with PARDISO")
                try:
                    sim.mw.solveroutine.set_solver(SolverPardiso(""))
                    mw_data = sim.mw.run_sweep()
                    self._log("  Fallback solver: PARDISO — success")
                except Exception as _p_err:
                    self._log(f"  WARNING: PARDISO failed ({_p_err}) — retrying with SuperLU")
                    try:
                        sim.mw.solveroutine.set_solver(SolverSuperLU(""))
                        mw_data = sim.mw.run_sweep()
                        self._log("  Fallback solver: SuperLU — success")
                    except Exception as _su_err:
                        self._log(f"  ERROR: SuperLU fallback failed: {_su_err}")
                        self._log("  Traceback:\n" + traceback.format_exc())
                        raise
            else:
                self._log(
                    f"  ERROR: FEM sweep failed with {type(_sweep_err).__name__}: "
                    f"{_emsg if _emsg else repr(_sweep_err)}"
                )
                self._log("  Traceback:\n" + traceback.format_exc())
                raise
        finally:
            _stop_fem_hb.set()
            _fem_hb.join(timeout=2)
        self._log(f"  Sweep done.  ({time.monotonic()-_t_sweep:.1f} s)")

        # ── Assemble S-matrix ─────────────────────────────────────────────────
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
            filename    = str(ts_path),
            freq        = np.array(freq_axis),
            Smat        = Smat,
            data_format = "RI",
            funit       = "GHz",
        )

        self._log("")
        self._log(_SEP)
        self._log("Stage 5 / 5 — Results")
        self._log(_SEP)
        self._log(f"Touchstone written: {ts_path}")

        if self.export_sparam_png:
            self._export_sparam_png(freq_axis, Smat)

        if self.export_field_html:
            self._export_field_html(mw_data, freq_axis)

        if self.show_field_animation:
            self._log("")
            self._log(_SEP)
            self._log("Stage 6 / 6 — Field animation")
            self._log(_SEP)
            _vtk_prev = None
            try:
                if not hasattr(mw_data, "field"):
                    raise RuntimeError("field data not available in sweep result")

                if len(freq_axis) == 0:
                    raise RuntimeError("frequency axis is empty")

                if self.field_animation_freq_hz > 0:
                    target_f = float(self.field_animation_freq_hz)
                else:
                    target_f = float(freq_axis[len(freq_axis) // 2])

                freq_arr = np.array(freq_axis, dtype=float)
                nearest_f = float(freq_arr[np.argmin(np.abs(freq_arr - target_f))])
                self._log(
                    f"  Field component: {self.field_component}  "
                    f"freq: {nearest_f/1e9:.6g} GHz"
                )

                # Silence noisy VTK/OpenGL warnings during interactive viewer init.
                try:
                    import vtk as _vtk
                    if hasattr(_vtk, "vtkObject"):
                        _vtk_prev = True
                        _vtk.vtkObject.GlobalWarningDisplayOff()
                    if hasattr(_vtk, "vtkLogger") and hasattr(_vtk.vtkLogger, "SetStderrVerbosity"):
                        try:
                            _vtk.vtkLogger.SetStderrVerbosity(_vtk.vtkLogger.VERBOSITY_OFF)
                        except Exception:
                            pass
                except Exception:
                    _vtk_prev = None

                # Use a thin horizontal cutplane near PCB mid-plane for stable visualization.
                cut = mw_data.field.find(freq=nearest_f).cutplane(0.1e-3, z=0.0)
                scalar = cut.scalar(self.field_component, "complex")
                with _suppress_native_output():
                    sim.display.animate().add_field(scalar, symmetrize=True)
                    self._log("Showing field animation — close the viewer window to continue ...")
                    sim.display.show()
            except Exception as exc:
                self._log(f"WARNING: field animation failed: {exc}")
            finally:
                if _vtk_prev:
                    try:
                        import vtk as _vtk
                        if hasattr(_vtk, "vtkObject"):
                            _vtk.vtkObject.GlobalWarningDisplayOn()
                    except Exception:
                        pass

        return ts_path


# =============================================================================
# EmergeReporter
# =============================================================================

class EmergeReporter:
    """
    Parse a Touchstone .s2p file and report insertion loss / return loss
    against configurable thresholds.
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

    def run(self) -> int:
        """Returns number of violations (0 = all pass, >0 = failures, -1 = error)."""
        if not self.ts_path.exists():
            self._log(f"ERROR: Touchstone file not found: {self.ts_path}")
            return -1

        ts    = emerge.TouchstoneData(str(self.ts_path))
        freqs = ts.f
        violations = 0
        s11_db_min = s21_db_max = None

        for k, f in enumerate(freqs):
            s11 = ts.S(1, 1)[k] if len(freqs) > 1 else ts.S(1, 1)
            s21 = ts.S(2, 1)[k] if len(freqs) > 1 else ts.S(2, 1)

            s11_db = 20 * math.log10(max(abs(s11), 1e-30))
            s21_db = 20 * math.log10(max(abs(s21), 1e-30))

            if -s21_db > self.il_threshold_db: violations += 1
            if  s11_db > -self.rl_threshold_db: violations += 1

            if s21_db_max is None or s21_db > s21_db_max:
                s21_db_max = s21_db
            if s11_db_min is None or s11_db < s11_db_min:
                s11_db_min = s11_db

        il_worst = -(s21_db_max or 0)
        rl_best  = -(s11_db_min or 0)

        self._log(f"S21: worst IL = {il_worst:.1f} dB  "
                  f"(threshold {self.il_threshold_db} dB)  "
                  f"{'PASS' if il_worst <= self.il_threshold_db else 'FAIL'}")
        self._log(f"S11: best  RL = {rl_best:.1f} dB  "
                  f"(threshold {self.rl_threshold_db} dB)  "
                  f"{'PASS' if rl_best >= self.rl_threshold_db else 'FAIL'}")
        self._log(f"Total violations: {violations}")
        return violations


# =============================================================================
# CLI entry point — invoked as subprocess by emerge_plugin.py
#
# Usage:
#   python emerge_runner.py --job job.json
#   python emerge_runner.py --pcb board.kicad_pcb [--config emerge_config.toml]
# =============================================================================

if __name__ == "__main__":
    import argparse
    import json

    def _job_from_pcb(pcb_path: str, config_path: str) -> dict:
        """Build a job dict from a .kicad_pcb + emerge_config.toml."""
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                raise RuntimeError(
                    "TOML support requires Python 3.11+ or: pip install tomli")

        cfg_path = pathlib.Path(config_path)
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config not found: {cfg_path}")

        with open(cfg_path, "rb") as fh:
            cfg = tomllib.load(fh)

        pcb     = pathlib.Path(pcb_path).resolve()
        out_dir = pcb.parent / "emerge_output"

        port_defs = {}
        for name, pd in cfg.get("ports", {}).items():
            port_defs[name] = {
                "pad":    pd["pad"],
                "R":      float(pd.get("R", 50.0)),
                "C":      pd.get("C"),
                "L":      pd.get("L"),
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
            "mesh":          cfg.get("mesh", {}),
        }

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
        """),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job", help="Path to job JSON written by emerge_plugin.py")
    group.add_argument("--pcb", help="Path to .kicad_pcb — standalone test mode")
    parser.add_argument("--config",
                        default=str(pathlib.Path(__file__).parent / "plugin" / "emerge_config.toml"),
                        help="TOML config (only used with --pcb)")
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

    if args.pcb:
        print(f"\nEMerge standalone test")
        print(f"  PCB   : {args.pcb}")
        print(f"  Config: {args.config}\n")
        try:
            job = _job_from_pcb(args.pcb, args.config)
        except Exception as exc:
            print(f"FATAL: {exc}")
            sys.exit(1)
        out_dir = pathlib.Path(job["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        job_path    = out_dir / "emerge_job.json"
        result_path = out_dir / "emerge_job.result.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        print(f"  Job written: {job_path}\n")
    else:
        job_path    = pathlib.Path(args.job)
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

    # ── run pipeline ───────────────────────────────────────────────────────────
    report_lines = []
    try:
        sweep        = job.get("sweep",         {})
        thresholds   = job.get("thresholds",    {})
        vis_cfg      = job.get("visualization", {})
        solver_cfg   = job.get("solver",        {})
        passives_cfg = job.get("passives",      {})
        gerber_cfg   = job.get("gerber",        {})
        mesh_cfg     = job.get("mesh",          {})

        use_gerbers  = bool (gerber_cfg.get("use_gerbers",                    False))
        use_keepout_bbox = bool(mesh_cfg.get("use_keepout_bbox",              True))
        repair_regions = bool(gerber_cfg.get("repair_regions",               True))
        cbr          = int  (mesh_cfg.get("curved_boundary_resolution",        40))
        max_mm       = float(mesh_cfg.get("max_mesh_size_mm",                   0))
        min_mm       = float(mesh_cfg.get("min_mesh_size_mm",                   0))
        port_focus   = bool (mesh_cfg.get("port_focus_only",                False))
        dom_margin   = float(mesh_cfg.get("domain_margin_mm",                   0))
        port_margin  = float(mesh_cfg.get("port_focus_margin_mm",               0))
        simplify_geo = bool (mesh_cfg.get("simplify_geometry",              False))
        simplify_k   = float(mesh_cfg.get("simplify_factor",                  1.0))
        pcb_split_z  = bool (mesh_cfg.get("pcb_split_z",                    True))
        pcb_merge    = bool (mesh_cfg.get("pcb_merge",                      True))
        circ_segs    = int  (mesh_cfg.get("gerber_circ_segments",              64))
        min_circ_segs = int (mesh_cfg.get("gerber_min_circ_segments",          24))
        res_mm       = float(mesh_cfg.get("gerber_res_mm",                   0.05))
        min_seg_um   = float(mesh_cfg.get("gerber_min_segment_um",            0.0))
        drop_zero_segs = bool(mesh_cfg.get("gerber_drop_zero_segments",      True))
        simplify_regions = bool(mesh_cfg.get("gerber_simplify_regions",     False))
        region_min_seg_um = float(mesh_cfg.get("gerber_region_min_segment_um", 0.0))
        sliver_mm    = float(mesh_cfg.get("sliver_threshold_mm",              0.10))
        algo_2d      = int  (mesh_cfg.get("algorithm_2d",                       6))
        algo_3d      = int  (mesh_cfg.get("algorithm_3d",                      10))
        smoothing    = int  (mesh_cfg.get("smoothing",                          10))
        max_retries  = int  (mesh_cfg.get("max_mesh_retries",                    8))
        art_um       = float(mesh_cfg.get("artifact_threshold_um",            50.0))
        cl_floor     = float(mesh_cfg.get("char_length_max_floor_mm",         0.15))
        cl_ceil      = float(mesh_cfg.get("char_length_max_ceil_mm",          0.50))
        cl_factor    = float(mesh_cfg.get("char_length_max_factor",           0.80))
        m_copper     = float(mesh_cfg.get("mesh_copper_mm",                   0.10))
        m_copper_z   = float(mesh_cfg.get("mesh_copper_z_mm",                 0.05))
        m_component  = float(mesh_cfg.get("mesh_component_mm",                0.10))
        m_substrate  = float(mesh_cfg.get("mesh_substrate_mm",                0.30))
        m_air        = float(mesh_cfg.get("mesh_air_mm",                      1.00))

        freq_start = float(sweep.get("start_hz",         1e6))
        freq_stop  = float(sweep.get("stop_hz",         10e9))
        freq_steps = int  (sweep.get("steps",            201))
        cpl        = int  (sweep.get("cells_per_lambda",  15))

        builder = EmergeModelBuilder(
            pcb_path                   = job["pcb_path"],
            gerber_dir                 = job["gerber_dir"],
            port_defs                  = job["port_defs"],
            show_geometry              = bool(vis_cfg.get("show_geometry", False)),
            geometry_viewer            = str(vis_cfg.get("geometry_viewer", "auto")),
            use_gerbers                = use_gerbers,
            model_passives             = bool(passives_cfg.get("model_passives", True)),
            skip_passives              = list(passives_cfg.get("skip", [])),
            curved_boundary_resolution = cbr,
            max_mesh_size_mm           = max_mm,
            min_mesh_size_mm           = min_mm,
            port_focus_only            = port_focus,
            use_keepout_bbox           = use_keepout_bbox,
            domain_margin_mm           = dom_margin,
            port_focus_margin_mm       = port_margin,
            simplify_geometry          = simplify_geo,
            simplify_factor            = simplify_k,
            pcb_split_z                = pcb_split_z,
            pcb_merge                  = pcb_merge,
            gerber_circ_segments       = circ_segs,
            gerber_min_circ_segments   = min_circ_segs,
            gerber_res_mm              = res_mm,
            gerber_min_segment_um      = min_seg_um,
            gerber_drop_zero_segments  = drop_zero_segs,
            gerber_simplify_regions    = simplify_regions,
            gerber_region_min_segment_um = region_min_seg_um,
            gerber_repair_regions       = repair_regions,
            sliver_threshold_mm        = sliver_mm,
            report_lines               = report_lines,
            verbose                    = True,
        )
        model = builder.run()
        if model is None:
            raise RuntimeError("Model build failed.")

        solver = EmergeSolver(
            model                      = model,
            output_dir                 = job["output_dir"],
            freq_start                 = freq_start,
            freq_stop                  = freq_stop,
            freq_steps                 = freq_steps,
            cells_per_lambda           = cpl,
            solver_engine              = solver_cfg.get("engine", "auto"),
            show_mesh                  = bool(vis_cfg.get("show_mesh", False)),
            mesh_viewer                = str(vis_cfg.get("mesh_viewer", "auto")),
            show_field_animation       = bool(vis_cfg.get("show_field_animation", False)),
            field_component            = str(vis_cfg.get("field_component", "Ez")),
            field_animation_freq_hz    = float(vis_cfg.get("field_animation_freq_hz", 0.0) or 0.0),
            export_sparam_png          = bool(vis_cfg.get("export_sparam_png", False)),
            export_field_html          = bool(vis_cfg.get("export_field_html", False)),
            curved_boundary_resolution = cbr,
            max_mesh_size_mm           = max_mm,
            min_mesh_size_mm           = min_mm,
            algorithm_2d               = algo_2d,
            algorithm_3d               = algo_3d,
            smoothing                  = smoothing,
            max_mesh_retries           = max_retries,
            artifact_threshold_um      = art_um,
            char_length_max_floor_mm   = cl_floor,
            char_length_max_ceil_mm    = cl_ceil,
            char_length_max_factor     = cl_factor,
            mesh_copper_mm             = m_copper,
            mesh_copper_z_mm           = m_copper_z,
            mesh_component_mm          = m_component,
            mesh_substrate_mm          = m_substrate,
            mesh_air_mm                = m_air,
            sliver_threshold_mm        = sliver_mm,
            report_lines               = report_lines,
            verbose                    = True,
        )
        ts_path = solver.run()
        if ts_path is None:
            raise RuntimeError("Solver failed.")

        reporter = EmergeReporter(
            touchstone_path = ts_path,
            il_threshold_db = float(thresholds.get("insertion_loss_db", 3.0)),
            rl_threshold_db = float(thresholds.get("return_loss_db",   10.0)),
            report_lines    = report_lines,
            verbose         = True,
        )
        violations = reporter.run()

        result = {"ts_path": str(ts_path), "violations": violations, "log": report_lines}

    except Exception as exc:
        _tb = traceback.format_exc()
        result = {
            "ts_path":    None,
            "violations": -1,
            "log":        report_lines + [
                f"FATAL: {type(exc).__name__}: {exc if str(exc) else repr(exc)}",
                "TRACEBACK:",
                _tb,
            ],
        }

    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.pcb:
        print(f"\n  Result written: {result_path}")
        print(f"  Violations    : {result['violations']}")

    sys.exit(0 if result["violations"] >= 0 else 1)
