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

# Force UTF-8 on stdout/stderr (Windows defaults to cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

# ── DEBUG flag ─────────────────────────────────────────────────────────────────
DEBUG: bool = os.environ.get("EMERGE_DEBUG", "0").strip() not in ("0", "", "false", "False")

def _dbg(msg: str, report_lines: list | None = None):
    line = f"  [DEBUG] {msg}"
    print(line)
    if report_lines is not None:
        report_lines.append(line)


# ── emerge imports ─────────────────────────────────────────────────────────────
_EMERGE_OK   = False
_EMERGE_ERR  = ""
_EMERGE_VER  = ""
_FILE_PCB_OK = False

try:
    import emerge
    _EMERGE_VER = getattr(emerge, "__version__", "?")

    from emerge import Simulation
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
                 use_gerbers=True,
                 model_passives=True, skip_passives=None,
                 curved_boundary_resolution=200,
                 max_mesh_size_mm=0.0, min_mesh_size_mm=0.0,
                 port_focus_only=False,
                 gerber_circ_segments=64,
                 gerber_res_mm=0.05,
                 sliver_threshold_mm=0.10,
                 report_lines=None, verbose=True):
        self.pcb_path                   = pathlib.Path(pcb_path)
        self.gerber_dir                 = pathlib.Path(gerber_dir)
        self.port_defs                  = port_defs
        self.show_geometry              = show_geometry
        self.use_gerbers                = use_gerbers
        self.model_passives             = model_passives
        self.skip_passives              = list(skip_passives or [])
        self.curved_boundary_resolution = int(curved_boundary_resolution)
        self.max_mesh_size_mm           = float(max_mesh_size_mm)
        self.min_mesh_size_mm           = float(min_mesh_size_mm)
        self.port_focus_only            = bool(port_focus_only)
        self.gerber_circ_segments       = int(gerber_circ_segments)
        self.gerber_res_mm              = float(gerber_res_mm)
        self.sliver_threshold_mm        = float(sliver_threshold_mm)
        self.report_lines               = report_lines if report_lines is not None else []
        self.verbose                    = verbose

        # Sanity-check: every attribute used in run() must be set here.
        _REQUIRED = [
            "pcb_path", "gerber_dir", "port_defs", "show_geometry",
            "use_gerbers", "model_passives", "skip_passives",
            "curved_boundary_resolution", "max_mesh_size_mm", "min_mesh_size_mm",
            "port_focus_only",
            "gerber_circ_segments", "gerber_res_mm", "sliver_threshold_mm",
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
                "y":      -y_mm * 1e-3,   # KiCad Y-down → Gerber Y-up (same flip as outline)
                "R":      float(pdef.get("R", 50.0)),
                "active": bool(pdef.get("active", True)),
            })
            self._log(f"  {pname}: pad={key}  ({x_mm:.3f}, {-y_mm:.3f}) mm [Gerber Y]  "
                      f"R={pdef.get('R',50)} Ω  active={pdef.get('active',True)}")

        if not ports:
            self._log("ERROR: No valid ports resolved — cannot build model.")
            return None

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
        # NOTE: read_board_outline() returns KiCad Y-down coordinates (mm).
        # Gerber files use Y-up (negated).  Domain bounds are passed to
        # pcb.set_bounds() which works in the same coordinate system as the
        # Gerbers, so we negate Y here.
        margin = max(0.005, board_t * 3)
        port_margin = max(0.001, board_t)

        if self.port_focus_only:
            xs_p = [p["x"] for p in ports]
            ys_p = [p["y"] for p in ports]
            xmin = min(xs_p) - port_margin;  xmax = max(xs_p) + port_margin
            ymin = min(ys_p) - port_margin;  ymax = max(ys_p) + port_margin
            self._log("Domain source: port_focus_only=true — using port-based bounds")
        else:
            ko_bbox = read_keepout_bbox(self.pcb_path)
            if ko_bbox is not None:
                kx0, ky0, kx1, ky1 = ko_bbox
                xmin = kx0 * 1e-3
                xmax = kx1 * 1e-3
                ymin = -ky1 * 1e-3  # KiCad Y-down -> Gerber Y-up
                ymax = -ky0 * 1e-3
                self._log(f"Domain source: keepout bbox ({kx0:.1f}, {ky0:.1f}) – "
                          f"({kx1:.1f}, {ky1:.1f}) mm")
            else:
                xs_p = [p["x"] for p in ports]
                ys_p = [p["y"] for p in ports]
                xmin = min(xs_p) - port_margin;  xmax = max(xs_p) + port_margin
                ymin = min(ys_p) - port_margin;  ymax = max(ys_p) + port_margin
                self._log("Domain source: keepout not found — using port-based bounds")

        # Expand domain to include every port (handles off-board components)
        for p in ports:
            if p["x"] - margin < xmin: xmin = p["x"] - margin
            if p["x"] + margin > xmax: xmax = p["x"] + margin
            if p["y"] - margin < ymin: ymin = p["y"] - margin
            if p["y"] + margin > ymax: ymax = p["y"] + margin

        self._log(f"Simulation domain: ({xmin*1e3:.1f}, {ymin*1e3:.1f}) – "
                  f"({xmax*1e3:.1f}, {ymax*1e3:.1f}) mm  margin={margin*1e3:.1f} mm")

        # ── Simulation domain as KiCad-coordinate bbox (for passive filtering) ─
        # Gerber Y-up (metres) → KiCad Y-down (mm): negate Y, scale ×1000
        # This rectangle is passed to PassiveElementModeler so only components
        # inside the simulation area are modelled — not the whole board.
        _sd_xmin_mm =  xmin * 1e3
        _sd_xmax_mm =  xmax * 1e3
        _sd_ymin_mm = -ymax * 1e3   # Gerber Y-up → KiCad Y-down: negate
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
                loaded_layers = load_copper_layers(
                    pcb        = pcb,
                    stackup    = stackup,
                    pcb_path   = self.pcb_path,
                    gerber_dir = self.gerber_dir,
                    circ_segs  = self.gerber_circ_segments,
                    res_mm     = self.gerber_res_mm,
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
            compound_sliver_surfaces(threshold_m=_sliver_m, log=self._log)
        else:
            # Inline fallback when gerber_builder is missing
            from emerge._emerge.geo.open_region import open_pml_region
            board_w = xmax - xmin
            board_h = ymax - ymin
            pml_h   = board_t * 4
            pml_xy  = max(0.005, min(board_w, board_h) * 0.15)
            pml_z   = max(0.005, pml_h * 0.5)
            try:
                pcb_vol = pcb.generate_pcb(split_z=True, merge=True)
            except TypeError:
                pcb_vol = pcb.generate_pcb()
            air_vol = pcb.generate_air(height=pml_h)
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
            sim.mw.bc.LumpedPort(
                face        = port_geo,
                port_number = port_num,
                Z0          = p["R"],
            )

        self._log(f"Model built successfully.  ({time.monotonic()-_t0:.1f} s)")

        # Optional interactive geometry viewer (blocks until closed)
        if self.show_geometry:
            self._log("Showing 3D geometry — close the viewer window to continue ...")
            if _GERBER_BUILDER_OK:
                gmsh_view_geometry("Geometry — close window to continue")
            else:
                try:
                    _sim_view(sim, plot_mesh=False, labels=True, bc=True)
                except Exception as exc:
                    self._log(f"WARNING: geometry viewer failed: {exc}")

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
            "cells_per_lambda", "solver_engine", "show_mesh",
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
        try:
            # NOTE: do NOT call occ.synchronize() here.
            for _, _stag in _gmsh.model.getEntities(2):
                _sx = _gmsh.model.getBoundingBox(2, _stag)
                if (_sx[3]-_sx[0]) < 1e-3 or (_sx[4]-_sx[1]) < 1e-3:
                    continue
                for _, _ct in _gmsh.model.getBoundary(
                        [(2, _stag)], oriented=False):
                    _cbb = _gmsh.model.getBoundingBox(1, abs(_ct))
                    _clen = ((_cbb[3]-_cbb[0])**2 +
                             (_cbb[4]-_cbb[1])**2 +
                             (_cbb[5]-_cbb[2])**2) ** 0.5
                    if _clen < _ARTIFACT_THRESHOLD:
                        _n_skipped_artifact += 1
                        continue
                    if _clen < _min_tiny_len:
                        _min_tiny_len = _clen
        except Exception as _se:
            self._log(f"  Tiny-curve scan warning: {_se}")
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

        self._log("Generating mesh …")
        _t_mesh = time.monotonic()
        _mesh_ok = False

        # ── Heartbeat helper — runs in a daemon thread during each mesh attempt ──
        import threading as _threading

        def _mesh_heartbeat(stop_evt: _threading.Event, log_fn, t0: float,
                            interval: float = 30.0):
            """Print elapsed time + latest GMSH log line every `interval` seconds."""
            try:
                _gmsh.logger.start()
            except Exception:
                pass
            while not stop_evt.wait(timeout=interval):
                elapsed = time.monotonic() - t0
                # Grab the most recent GMSH internal log message (if any)
                try:
                    msgs = _gmsh.logger.get()
                    last_msg = msgs[-1].strip() if msgs else ""
                except Exception:
                    last_msg = ""
                if last_msg:
                    log_fn(f"  [mesh] still running … {elapsed:.0f} s  | {last_msg[:120]}")
                else:
                    log_fn(f"  [mesh] still running … {elapsed:.0f} s")
            try:
                _gmsh.logger.stop()
            except Exception:
                pass

        for _attempt in range(_MAX_MESH_RETRIES + 1):
            # Re-apply any accumulated surface-fix constraints after clear
            _gmsh.model.mesh.clear()
            for _ec_tag, _ec_n in _extra_constraints:
                try:
                    _gmsh.model.mesh.setTransfiniteCurve(_ec_tag, _ec_n)
                except Exception:
                    pass
            _stop_hb = _threading.Event()
            _hb_thread = _threading.Thread(
                target=_mesh_heartbeat,
                args=(_stop_hb, self._log, _t_mesh),
                daemon=True,
            )
            _hb_thread.start()
            try:
                sim.generate_mesh()
                _stop_hb.set()
                _hb_thread.join(timeout=2)
                self._log(f"  Mesh done.  ({time.monotonic()-_t_mesh:.1f} s)")
                _mesh_ok = True
                break
            except Exception as _mesh_exc:
                _stop_hb.set()
                _hb_thread.join(timeout=2)
                _mesh_msg = str(_mesh_exc)

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
            if _GERBER_BUILDER_OK:
                gmsh_view_mesh("Mesh — close window to continue")
            else:
                try:
                    _sim_view(sim, plot_mesh=True)
                except Exception as exc:
                    self._log(f"WARNING: mesh viewer failed: {exc}")

        self._log("")
        self._log(_SEP)
        self._log("Stage 4 / 5 — Running FEM sweep")
        self._log(_SEP)
        self._log(f"  Frequency: {self.freq_start/1e6:.0f} MHz – "
                  f"{self.freq_stop/1e9:.1f} GHz  ({self.freq_steps} points)")

        _t_sweep = time.monotonic()
        self._log("Running FEM sweep …")
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
                    sim.mw.solveroutine.set_solver(SolverSuperLU(""))
                    mw_data = sim.mw.run_sweep()
                    self._log("  Fallback solver: SuperLU — success")
            else:
                raise
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
        cbr          = int  (mesh_cfg.get("curved_boundary_resolution",        40))
        max_mm       = float(mesh_cfg.get("max_mesh_size_mm",                   0))
        min_mm       = float(mesh_cfg.get("min_mesh_size_mm",                   0))
        port_focus   = bool (mesh_cfg.get("port_focus_only",                False))
        circ_segs    = int  (mesh_cfg.get("gerber_circ_segments",              64))
        res_mm       = float(mesh_cfg.get("gerber_res_mm",                   0.05))
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
            use_gerbers                = use_gerbers,
            model_passives             = bool(passives_cfg.get("model_passives", True)),
            skip_passives              = list(passives_cfg.get("skip", [])),
            curved_boundary_resolution = cbr,
            max_mesh_size_mm           = max_mm,
            min_mesh_size_mm           = min_mm,
            port_focus_only            = port_focus,
            gerber_circ_segments       = circ_segs,
            gerber_res_mm              = res_mm,
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
