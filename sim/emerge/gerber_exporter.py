"""
gerber_exporter.py — Export Gerbers from a .kicad_pcb via kicad-cli.

Part of the EMerge FEM simulation pipeline.
Wraps kicad-cli gerber export; auto-detects kicad-cli on Windows.

Author: Author
Version: 1.1.0
"""

import os
import pathlib
import shutil
import subprocess


# ── kicad-cli detection ──────────────────────────────────────────────────────

def _iter_kicad_cli_candidates():
    """
    Yield kicad-cli candidate paths in priority order.

    1. All versions found under C:\\Program Files\\KiCad\\  (newest version first)
       — covers KiCad 8, 9, 10, … without hardcoding version numbers.
    2. kicad-cli on PATH (Linux / macOS).
    """
    import platform as _platform
    if _platform.system() == "Windows":
        pf = pathlib.Path(r"C:\Program Files\KiCad")
        if pf.is_dir():
            # Sort version folders numerically (10.0 > 9.0 > 8.0)
            def _ver_key(p):
                try:
                    parts = [int(x) for x in p.name.split(".")]
                    return parts
                except ValueError:
                    return [0]
            dirs = sorted(
                [d for d in pf.iterdir() if d.is_dir()],
                key=_ver_key, reverse=True,
            )
            for d in dirs:
                cli = d / "bin" / "kicad-cli.exe"
                if cli.exists():
                    yield str(cli)
    # PATH fallback (Linux / macOS / manually installed)
    yield "kicad-cli"


def _find_kicad_cli(override=""):
    """
    Return the path to kicad-cli to use for Gerber export.

    If *override* is a non-empty string it is used directly (must point to the
    kicad-cli binary OR the KiCad bin directory — both are accepted).
    Otherwise the newest installed version is returned.
    """
    if override:
        p = pathlib.Path(str(override))
        # Accept either the exe itself or its parent bin/ directory
        if p.is_dir():
            cli = p / "kicad-cli.exe"
            if cli.exists():
                return str(cli)
            return None
        if p.exists() and p.is_file():
            return str(p)
        if shutil.which(str(override)):
            return str(override)
        # An explicit override was supplied but does not exist. Do not fall
        # back to auto-detection, because that hides configuration mistakes.
        return None
    for c in _iter_kicad_cli_candidates():
        if shutil.which(c) or pathlib.Path(c).exists():
            return c
    return None


# ── GerberExporter ───────────────────────────────────────────────────────────

DEBUG: bool = os.environ.get("EMERGE_DEBUG", "0").strip() not in ("0", "", "false", "False")


class GerberExporter:
    """
    Export Gerber files from a .kicad_pcb using kicad-cli.

    Args:
        pcb_path      : Path to .kicad_pcb file
        output_dir    : Directory to write Gerbers into (created if needed)
        kicad_cli     : Path to kicad-cli executable (auto-detected if empty)
        report_lines  : Shared list for log messages
        verbose       : Print progress to stdout
        copper_layers : List of copper layer names to export (e.g. ["F.Cu",
                        "In1.Cu", "In2.Cu", "B.Cu"]).  When None the default
                        two-layer list ["F.Cu", "B.Cu"] is used.  Pass the
                        full list from pcbnew when the board has inner layers.
    """

    _BASE_NON_COPPER = [
        "F.Mask", "B.Mask",
        "F.Silkscreen", "B.Silkscreen",
        "Edge.Cuts",
    ]
    _DEFAULT_COPPER = ["F.Cu", "B.Cu"]

    def __init__(self, pcb_path, output_dir,
                 kicad_cli="", report_lines=None, verbose=True,
                 copper_layers=None):
        self.pcb_path     = pathlib.Path(pcb_path)
        self.output_dir   = pathlib.Path(output_dir)
        self.kicad_cli    = _find_kicad_cli(kicad_cli)
        self.report_lines = report_lines if report_lines is not None else []
        self.verbose      = verbose
        _cu = list(copper_layers) if copper_layers else list(self._DEFAULT_COPPER)
        self.LAYERS = _cu + self._BASE_NON_COPPER

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def run(self):
        """
        Export Gerbers. Returns list of exported file paths on success, [] on failure.
        """
        self._log(f"  PCB file   : {self.pcb_path}")
        self._log(f"  Output dir : {self.output_dir}")
        self._log(f"  kicad-cli  : {self.kicad_cli or '(not found)'}")
        self._log(f"  Layers     : {', '.join(self.LAYERS)}")

        if not self.pcb_path.exists():
            self._log(f"ERROR: PCB file not found: {self.pcb_path}")
            return []

        pcb_size_kb = self.pcb_path.stat().st_size / 1024
        self._log(f"  PCB size   : {pcb_size_kb:.0f} kB")

        if not self.kicad_cli:
            self._log("ERROR: kicad-cli not found. Install KiCad or set kicad_cli path in config.")
            return []

        self.output_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.kicad_cli, "pcb", "export", "gerbers",
            "--output", str(self.output_dir),
            "--layers", ",".join(self.LAYERS),
            # EMerge 2.8 parser is more reliable with plain Gerber output:
            # disable X2 attributes, embedded netlist attributes and aperture
            # macros generated by KiCad for some pad/shape constructs.
            "--no-x2",
            "--no-netlist",
            "--disable-aperture-macros",
            "--no-protel-ext",
            str(self.pcb_path),
        ]

        self._log(f"  Command    : {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Always show kicad-cli output in debug mode
            if DEBUG:
                if result.stdout.strip():
                    for line in result.stdout.strip().splitlines():
                        self._log(f"    [kicad-cli stdout] {line}")
                if result.stderr.strip():
                    for line in result.stderr.strip().splitlines():
                        self._log(f"    [kicad-cli stderr] {line}")

            if result.returncode != 0:
                self._log(f"ERROR: kicad-cli exited with code {result.returncode}")
                # Always show stderr on failure even without debug
                if result.stderr.strip() and not DEBUG:
                    self._log(result.stderr.strip())
                return []

            # Also export Excellon drills so vias/PTH holes can be reconstructed.
            drill_cmd = [
                self.kicad_cli, "pcb", "export", "drill",
                "--output", str(self.output_dir),
                "--format", "excellon",
                "--drill-origin", "absolute",
                "--excellon-units", "mm",
                "--excellon-separate-th",
                str(self.pcb_path),
            ]
            self._log(f"  Drill cmd  : {' '.join(drill_cmd)}")
            drill_result = subprocess.run(
                drill_cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if DEBUG:
                if drill_result.stdout.strip():
                    for line in drill_result.stdout.strip().splitlines():
                        self._log(f"    [kicad-cli drill stdout] {line}")
                if drill_result.stderr.strip():
                    for line in drill_result.stderr.strip().splitlines():
                        self._log(f"    [kicad-cli drill stderr] {line}")
            if drill_result.returncode != 0:
                self._log(f"  WARNING: drill export failed (code {drill_result.returncode}); continuing without drill files")
                if drill_result.stderr.strip() and not DEBUG:
                    self._log(drill_result.stderr.strip())

            # Collect exported files
            exported = []
            for ext in ("*.gbr", "*.gtl", "*.gbl", "*.drl"):
                exported.extend(self.output_dir.glob(ext))

            self._log(f"  Exported {len(exported)} file(s):")
            for f in sorted(exported):
                size_kb = f.stat().st_size / 1024
                self._log(f"    {f.name:<50}  {size_kb:6.1f} kB")

            if not exported:
                self._log("  WARNING: kicad-cli succeeded but no Gerber files found in output dir")
                self._log(f"  Output dir contents: {[p.name for p in self.output_dir.iterdir()]}")

            return exported

        except subprocess.TimeoutExpired:
            self._log("ERROR: kicad-cli timed out after 120 s")
            return []
        except FileNotFoundError:
            self._log(f"ERROR: kicad-cli executable not found: {self.kicad_cli}")
            return []
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            return []
