"""
emerge_pipeline.py — Standalone EMerge FEM pipeline for ad8317_powermeter.

Drives the complete KiCad PCB → Gerber → EMerge → Touchstone workflow from
the command line.  KiCad does not need to be open.

Uses the FileBasedPCB API (emerge.beta.gerber) — this is the standalone path.
The KiCad Action Plugin uses PCBNew (emerge._emerge.geo.pcb) instead.

Geometry and material data come exclusively from the .kicad_pcb stackup block —
the PCB file is the single source of truth.

Usage:
    python emerge_pipeline.py [--pcb PATH] [--config PATH] [--out DIR] [--verbose]

Author: Author
Version: 1.1.0
"""

import math
import os
import sys
import argparse
import pathlib
from datetime import datetime

# ── emerge (graceful fallback so module loads without EMerge installed) ────────
try:
    import emerge
    HAS_EMERGE = True
except ImportError:
    print("WARNING: emerge package not found — simulation steps will be skipped.")
    emerge = None
    HAS_EMERGE = False

# ── canonical support modules (single source of truth) ────────────────────────
# kicad_reader and gerber_exporter live alongside this file in sim/emerge/.
# We alias them to the private names used in this module so tests keep working.
try:
    from kicad_reader import read_pad_positions as _get_pad_positions, \
                             read_stackup      as _read_stackup
except ImportError:
    def _get_pad_positions(p): return {}    # type: ignore
    def _read_stackup(p): return {}         # type: ignore
    print("WARNING: kicad_reader not found — PCB reader functions unavailable.")

try:
    from gerber_exporter import GerberExporter
except ImportError:
    GerberExporter = None  # type: ignore
    print("WARNING: gerber_exporter not found — Gerber export unavailable.")

# ── TOML support (Python 3.11+ has tomllib built-in) ──────────────────────────
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        try:
            import toml as tomllib
        except ImportError:
            print("WARNING: No TOML library — config file disabled. pip install tomli")
            tomllib = None

# ── paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = pathlib.Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent.parent                    # …/ad8317_powermeter/
KICAD_DIR   = PROJECT_DIR / "kicad"
RESULTS_DIR = SCRIPT_DIR / "results"

# Single config — lives in plugin/ (deployed to KiCad plugin folder from there)
DEFAULT_CONFIG = SCRIPT_DIR / "plugin" / "emerge_config.toml"

PCB_FILE  = KICAD_DIR / "ad8317_powermeter.kicad_pcb"

# Module-level sweep defaults (overridden by config if present)
FREQ_START_HZ    = 1e6
FREQ_STOP_HZ     = 10e9
FREQ_STEPS       = 201
CELLS_PER_LAMBDA = 15

PORT_DEFS = {
    "PORT1": {"pad": "J1:1",  "R": 50.0,  "C": None,  "active": True,  "dir": "z"},
    "PORT2": {"pad": "J2:1",  "R": 50.0,  "C": None,  "active": False, "dir": "z"},
    "PORT3": {"pad": "U2:8",  "R": 200.0, "C": 2e-12, "active": False, "dir": "z"},
}


# =========================================================================== #
# Configuration loader
# =========================================================================== #

def load_config(config_path=None) -> dict:
    """Load emerge_config.toml; return {} if absent or unparseable."""
    if tomllib is None:
        return {}
    path = pathlib.Path(config_path) if config_path else DEFAULT_CONFIG
    if not path.exists():
        print(f"WARNING: Config not found at {path} — using module defaults.")
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:
        print(f"WARNING: Could not parse {path}: {exc}")
        return {}


def _port_defs_from_config(cfg: dict) -> dict:
    """Convert config [ports.*] sections to the PORT_DEFS dict format."""
    ports_cfg = cfg.get("ports", {})
    if not ports_cfg:
        return PORT_DEFS
    result = {}
    for name, p in ports_cfg.items():
        c_val = p.get("C", 0)
        result[name.upper()] = {
            "pad":    p.get("pad", ""),
            "R":      float(p.get("R", 50.0)),
            "C":      float(c_val) if c_val else None,
            "active": bool(p.get("active", True)),
            "dir":    str(p.get("dir", "z")),
        }
    return result


# =========================================================================== #
# EmergeModelBuilder — builds model from Gerbers using FileBasedPCB API
# =========================================================================== #

class EmergeModelBuilder:
    """
    Load Gerbers into EMerge via FileBasedPCB, apply stackup from KiCad PCB,
    define ports from ref:pad notation, and add PML air box.

    This uses the FileBasedPCB (emerge.beta.gerber) approach — the standalone
    path.  The KiCad Action Plugin (emerge_runner.py) uses PCBNew instead.

    Args:
        pcb_path    : Path to .kicad_pcb  (material/geometry source of truth)
        gerber_dir  : Directory containing exported Gerber files
        port_defs   : Dict of port definitions (ref:pad, R, C, active, dir)
        report_lines: Shared list for log messages
        verbose     : Print progress to stdout
    """

    def __init__(self, pcb_path, gerber_dir, port_defs=None,
                 report_lines=None, verbose=True):
        self.pcb_path     = pathlib.Path(pcb_path)
        self.gerber_dir   = pathlib.Path(gerber_dir)
        self.port_defs    = port_defs or PORT_DEFS
        self.report_lines = report_lines if report_lines is not None else []
        self.verbose      = verbose
        self.stackup      = None
        self.pad_map      = {}
        self.model        = None
        self.port_count   = 0

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg)

    def _resolve_port_position(self, ref_pad):
        """Return (x_mm, y_mm) for ref:pad, or raise KeyError with suggestions."""
        if ref_pad not in self.pad_map:
            ref = ref_pad.split(":")[0]
            available = [k for k in self.pad_map if k.split(":")[0] == ref]
            raise KeyError(
                f"Pad '{ref_pad}' not found in PCB. "
                f"Available pads for {ref}: {available}")
        return self.pad_map[ref_pad]

    def run(self):
        """Build and return the EMerge PCB model, or None on failure."""
        if not HAS_EMERGE:
            self._log("ERROR: EMerge not available — cannot build model.")
            return None

        self._log("[EmergeModelBuilder] Reading PCB data ...")
        self.stackup = _read_stackup(self.pcb_path)
        self.pad_map = _get_pad_positions(self.pcb_path)
        self._log(f"  Stackup: {self.stackup['copper_layers']}-layer, "
                  f"{self.stackup['board_thickness_mm']:.2f} mm, "
                  f"er={self.stackup['er']}, tand={self.stackup['tand']}")
        self._log(f"  Pad map: {len(self.pad_map)} pads resolved")

        self._log(f"[EmergeModelBuilder] Loading Gerbers from {self.gerber_dir} ...")
        try:
            pcb = emerge.beta.gerber.FileBasedPCB(str(self.gerber_dir))
        except Exception as exc:
            self._log(f"ERROR: FileBasedPCB failed: {exc}")
            return None

        st = self.stackup
        try:
            pcb.set_dielectric(thickness_mm=st["board_thickness_mm"],
                               er=st["er"], tand=st["tand"])
            pcb.set_copper_thickness_mm(st["copper_thickness_mm"])
            self._log("  Stackup applied from KiCad PCB.")
        except AttributeError:
            self._log("  WARNING: emerge PCB stackup API not available — "
                      "check EMerge version.")

        try:
            pcb.open_pml_region()
            self._log("  PML air box added (auto-sized from geometry bbox).")
        except Exception as exc:
            self._log(f"  WARNING: open_pml_region failed: {exc}")

        self._log("[EmergeModelBuilder] Defining ports ...")
        for port_name, cfg in self.port_defs.items():
            ref_pad = cfg["pad"]
            try:
                x_mm, y_mm = self._resolve_port_position(ref_pad)
            except KeyError as exc:
                self._log(f"  WARNING: {exc} — skipping {port_name}")
                continue

            R      = cfg.get("R", 50.0)
            C      = cfg.get("C")
            active = cfg.get("active", True)
            dir_ax = getattr(emerge, cfg.get("dir", "z").upper() + "AX", emerge.ZAX)

            try:
                impedance = (emerge.parallel_impedance(R=R, C=C) if C is not None
                             else emerge.series_impedance(R=R))
                pcb.add_port(name=port_name, x_mm=x_mm, y_mm=y_mm,
                             impedance=impedance, active=active, direction=dir_ax)
                c_str = f" || C={C*1e12:.1f}pF" if C is not None else ""
                self._log(f"  {port_name}: {ref_pad} @ ({x_mm:.2f},{y_mm:.2f}) mm  "
                          f"R={R}Ohm{c_str}  active={active}")
                self.port_count += 1
            except Exception as exc:
                self._log(f"  WARNING: Could not add port {port_name}: {exc}")

        self._log(f"  {self.port_count}/{len(self.port_defs)} ports added.")
        self.model = pcb
        return pcb


# =========================================================================== #
# EmergeSolver
# =========================================================================== #

class EmergeSolver:
    """
    Configure frequency sweep, run EMerge FEM solve, export Touchstone.

    Args:
        model            : EMerge PCB model from EmergeModelBuilder.run()
        output_dir       : Directory for Touchstone output
        freq_start       : Start frequency [Hz]
        freq_stop        : Stop frequency [Hz]
        freq_steps       : Number of frequency points
        cells_per_lambda : Mesh density (cells per wavelength at f_stop)
        report_lines     : Shared list for log messages
        verbose          : Print progress to stdout
    """

    def __init__(self, model, output_dir,
                 freq_start=FREQ_START_HZ, freq_stop=FREQ_STOP_HZ,
                 freq_steps=FREQ_STEPS, cells_per_lambda=CELLS_PER_LAMBDA,
                 report_lines=None, verbose=True):
        self.model           = model
        self.output_dir      = pathlib.Path(output_dir)
        self.freq_start      = freq_start
        self.freq_stop       = freq_stop
        self.freq_steps      = freq_steps
        self.cells_per_lambda = cells_per_lambda
        self.report_lines    = report_lines if report_lines is not None else []
        self.verbose         = verbose
        self.touchstone_path = None

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg)

    def run(self):
        """Run solver and export Touchstone. Returns path or None."""
        if not HAS_EMERGE:
            self._log("ERROR: EMerge not available — solver skipped.")
            return None
        if self.model is None:
            self._log("ERROR: No model provided to solver.")
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        ts_path = self.output_dir / "ad8317_rf_path.s2p"

        self._log("[EmergeSolver] Configuring frequency sweep ...")
        self._log(f"  {self.freq_start/1e6:.0f} MHz -> {self.freq_stop/1e9:.1f} GHz, "
                  f"{self.freq_steps} points  ({self.cells_per_lambda} cells/lambda)")

        try:
            self.model.set_frequency_sweep(f_start=self.freq_start,
                                           f_stop=self.freq_stop,
                                           n_points=self.freq_steps)
            self.model.set_mesh_density(cells_per_lambda=self.cells_per_lambda)
        except Exception as exc:
            self._log(f"  WARNING: Sweep/mesh config failed: {exc}")

        self._log("[EmergeSolver] Running FEM solve (may take several minutes) ...")
        try:
            self.model.solve()
            self._log("  Solve complete.")
        except Exception as exc:
            self._log(f"ERROR: Solver failed: {exc}")
            return None

        self._log(f"[EmergeSolver] Exporting Touchstone to {ts_path} ...")
        try:
            self.model.export_touchstone(str(ts_path))
            self.touchstone_path = ts_path
            self._log(f"  Exported: {ts_path.name}")
        except Exception as exc:
            self._log(f"ERROR: Touchstone export failed: {exc}")
            return None

        return ts_path


# =========================================================================== #
# EmergeReporter
# =========================================================================== #

class EmergeReporter:
    """
    Parse a Touchstone .s2p file and report insertion loss / return loss
    against configurable thresholds.

    This is format-independent (MA, DB, RI) and has no emerge dependency.

    Args:
        touchstone_path  : Path to .s2p file
        il_threshold_db  : Insertion loss limit (IL > threshold → violation)
        rl_threshold_db  : Return loss minimum (RL < threshold → violation)
        report_lines     : Shared list for log messages
        verbose          : Print progress to stdout
    """

    def __init__(self, touchstone_path, report_lines=None, verbose=True,
                 il_threshold_db=3.0, rl_threshold_db=10.0):
        self.touchstone_path = pathlib.Path(touchstone_path)
        self.report_lines    = report_lines if report_lines is not None else []
        self.verbose         = verbose
        self.il_threshold_db = il_threshold_db
        self.rl_threshold_db = rl_threshold_db
        self.violation_count = 0

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg)

    def _parse_s2p(self):
        """Parse 2-port Touchstone. Returns list of (f_hz, S11_db, S21_db)."""
        rows = []
        freq_unit = 1.0
        fmt = "ri"
        with open(self.touchstone_path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("!"):
                    continue
                if line.startswith("#"):
                    parts = line.lower().split()
                    freq_unit = (1e9 if "ghz" in parts else
                                 1e6 if "mhz" in parts else
                                 1e3 if "khz" in parts else 1.0)
                    fmt = "ma" if "ma" in parts else ("db" if "db" in parts else "ri")
                    continue
                vals = line.split()
                if len(vals) >= 5:
                    f = float(vals[0]) * freq_unit
                    v1, v2, v3, v4 = (float(vals[1]), float(vals[2]),
                                      float(vals[3]), float(vals[4]))
                    if fmt == "ma":
                        s11_db = 20 * math.log10(max(v1, 1e-15))
                        s21_db = 20 * math.log10(max(v3, 1e-15))
                    elif fmt == "db":
                        s11_db, s21_db = v1, v3
                    else:
                        s11_db = 20 * math.log10(max(math.hypot(v1, v2), 1e-15))
                        s21_db = 20 * math.log10(max(math.hypot(v3, v4), 1e-15))
                    rows.append((f, s11_db, s21_db))
        return rows

    def run(self):
        """Parse Touchstone and check thresholds. Returns violation count."""
        if not self.touchstone_path.exists():
            self._log(f"ERROR: Touchstone file not found: {self.touchstone_path}")
            return 1

        self._log(f"\n[EmergeReporter] Results from {self.touchstone_path.name}")
        self._log("=" * 60)

        rows = self._parse_s2p()
        if not rows:
            self._log("  WARNING: No data parsed from Touchstone file.")
            return 1

        f_hz_list = [r[0] for r in rows]
        s11_list  = [r[1] for r in rows]
        s21_list  = [r[2] for r in rows]

        il_min = min(s21_list)
        rl_max = max(s11_list)
        f_il   = f_hz_list[s21_list.index(il_min)]
        f_rl   = f_hz_list[s11_list.index(rl_max)]

        self._log(f"  Frequency range  : {f_hz_list[0]/1e6:.1f} MHz – "
                  f"{f_hz_list[-1]/1e9:.2f} GHz  ({len(rows)} points)")
        self._log(f"  Worst IL (S21)   : {il_min:.2f} dB  @ {f_il/1e9:.3f} GHz")
        self._log(f"  Worst RL (S11)   : {rl_max:.2f} dB  @ {f_rl/1e9:.3f} GHz")

        violations = []
        for f, s11, s21 in rows:
            if s21 < -self.il_threshold_db:
                violations.append(
                    f"  FAIL IL  {f/1e9:.3f} GHz: S21={s21:.2f} dB "
                    f"(threshold -{self.il_threshold_db:.0f} dB)")
                self.violation_count += 1
            if s11 > -self.rl_threshold_db:
                violations.append(
                    f"  FAIL RL  {f/1e9:.3f} GHz: S11={s11:.2f} dB "
                    f"(threshold <-{self.rl_threshold_db:.0f} dB)")
                self.violation_count += 1

        if violations:
            self._log(f"\n  {len(violations)} threshold violation(s):")
            for v in violations[:20]:
                self._log(v)
            if len(violations) > 20:
                self._log(f"  ... and {len(violations) - 20} more.")
        else:
            self._log("  All thresholds PASSED.")

        self._log("=" * 60)
        return self.violation_count


# =========================================================================== #
# Pipeline orchestrator
# =========================================================================== #

def run_pipeline(pcb_path=None, output_dir=None, config_path=None,
                 freq_stop_ghz=None, verbose=True):
    """
    Run the complete 4-step pipeline:
      1. Export Gerbers from KiCad PCB (via kicad-cli)
      2. Build EMerge model (FileBasedPCB + stackup + ports)
      3. Run FEM solver → Touchstone
      4. Parse results → pass/fail report

    Returns (touchstone_path, violation_count) or (None, -1) on hard failure.
    """
    if GerberExporter is None:
        print("FATAL: gerber_exporter module not found.")
        return None, -1

    pcb_path   = pathlib.Path(pcb_path)   if pcb_path   else PCB_FILE
    output_dir = pathlib.Path(output_dir) if output_dir else RESULTS_DIR

    cfg      = load_config(config_path)
    sw       = cfg.get("sweep", {})
    th       = cfg.get("thresholds", {})
    ports    = _port_defs_from_config(cfg)

    f_start  = float(sw.get("start_hz",          FREQ_START_HZ))
    f_stop   = float(sw.get("stop_hz",            FREQ_STOP_HZ))
    steps    = int  (sw.get("steps",              FREQ_STEPS))
    cpl      = int  (sw.get("cells_per_lambda",   CELLS_PER_LAMBDA))
    il_thr   = float(th.get("insertion_loss_db",  3.0))
    rl_thr   = float(th.get("return_loss_db",    10.0))

    if freq_stop_ghz:
        f_stop = freq_stop_ghz * 1e9

    report_lines = [
        f"EMerge Pipeline v1.1.0 — {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"PCB:    {pcb_path}",
        f"Output: {output_dir}",
        "",
    ]

    # Step 1 — Gerbers
    gerber_dir = output_dir / "gerbers"
    ger_cfg    = cfg.get("gerber", {})
    kicad_cli  = ger_cfg.get("kicad_cli") or None
    exporter   = GerberExporter(
        pcb_path=pcb_path, output_dir=gerber_dir,
        kicad_cli=kicad_cli,
        report_lines=report_lines, verbose=verbose)
    if not exporter.run():
        print("Pipeline aborted: Gerber export failed.")
        return None, -1

    # Step 2 — Model
    builder = EmergeModelBuilder(
        pcb_path=pcb_path, gerber_dir=gerber_dir,
        port_defs=ports, report_lines=report_lines, verbose=verbose)
    model = builder.run()
    if model is None:
        print("Pipeline aborted: Model build failed.")
        return None, -1

    # Step 3 — Solve
    solver = EmergeSolver(
        model=model, output_dir=output_dir / "touchstone",
        freq_start=f_start, freq_stop=f_stop,
        freq_steps=steps, cells_per_lambda=cpl,
        report_lines=report_lines, verbose=verbose)
    ts_path = solver.run()
    if ts_path is None:
        print("Pipeline aborted: Solver failed.")
        return None, -1

    # Step 4 — Report
    reporter = EmergeReporter(
        touchstone_path=ts_path,
        il_threshold_db=il_thr, rl_threshold_db=rl_thr,
        report_lines=report_lines, verbose=verbose)
    violations = reporter.run()

    report_path = output_dir / "emerge_report.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\nReport written to: {report_path}")

    return ts_path, violations


# =========================================================================== #
# CLI entry point
# =========================================================================== #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Standalone EMerge FEM pipeline — ad8317_powermeter RF path")
    parser.add_argument("--pcb",      default=None,
                        help="Path to .kicad_pcb (default: kicad/ad8317_powermeter.kicad_pcb)")
    parser.add_argument("--config",   default=None,
                        help="Path to emerge_config.toml (default: plugin/emerge_config.toml)")
    parser.add_argument("--out",      default=None,
                        help="Output directory (default: sim/emerge/results/)")
    parser.add_argument("--freq-ghz", type=float, default=None,
                        help="Upper frequency limit in GHz (overrides config)")
    parser.add_argument("--quiet",    action="store_true",
                        help="Suppress per-step output")
    args = parser.parse_args()

    ts, n = run_pipeline(
        pcb_path=args.pcb,
        output_dir=args.out,
        config_path=args.config,
        freq_stop_ghz=args.freq_ghz,
        verbose=not args.quiet,
    )
    sys.exit(0 if (ts is not None and n == 0) else 1)
