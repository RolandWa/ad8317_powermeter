"""
gerber_exporter.py — Export Gerbers from a .kicad_pcb via kicad-cli.

Part of the EMerge FEM simulation pipeline.
Wraps kicad-cli gerber export; auto-detects kicad-cli on Windows.

Author: Author
Version: 1.0.0
"""

import pathlib
import shutil
import subprocess


# ── kicad-cli detection ──────────────────────────────────────────────────────

_KICAD_CLI_CANDIDATES = [
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
    "kicad-cli",   # on PATH (Linux/Mac)
]

def _find_kicad_cli(override=""):
    if override:
        p = pathlib.Path(str(override))
        return str(p) if p.exists() else None   # explicit path missing → hard failure
    for c in _KICAD_CLI_CANDIDATES:
        if shutil.which(c) or pathlib.Path(c).exists():
            return c
    return None


# ── GerberExporter ───────────────────────────────────────────────────────────

class GerberExporter:
    """
    Export Gerber files from a .kicad_pcb using kicad-cli.

    Args:
        pcb_path   : Path to .kicad_pcb file
        output_dir : Directory to write Gerbers into (created if needed)
        kicad_cli  : Path to kicad-cli executable (auto-detected if empty)
        report_lines: Shared list for log messages
        verbose    : Print progress to stdout
    """

    LAYERS = [
        "F.Cu", "B.Cu",
        "F.Mask", "B.Mask",
        "F.Silkscreen", "B.Silkscreen",
        "Edge.Cuts",
    ]

    def __init__(self, pcb_path, output_dir,
                 kicad_cli="", report_lines=None, verbose=True):
        self.pcb_path    = pathlib.Path(pcb_path)
        self.output_dir  = pathlib.Path(output_dir)
        self.kicad_cli   = _find_kicad_cli(kicad_cli)
        self.report_lines = report_lines if report_lines is not None else []
        self.verbose     = verbose

    def _log(self, msg):
        self.report_lines.append(msg)
        if self.verbose:
            print(msg)

    def run(self):
        """
        Export Gerbers. Returns list of exported file paths on success, [] on failure.
        The return value is truthy on success and falsy on failure, so callers can
        use it as a bool (if not exporter.run()) or inspect the file list.
        """
        if not self.pcb_path.exists():
            self._log(f"ERROR: PCB file not found: {self.pcb_path}")
            return []

        if not self.kicad_cli:
            self._log("ERROR: kicad-cli not found. Install KiCad or set kicad_cli path in config.")
            return []

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._log(f"Exporting Gerbers: {self.pcb_path.name} -> {self.output_dir}")

        cmd = [
            self.kicad_cli, "pcb", "export", "gerbers",
            "--output", str(self.output_dir),
            "--layers", ",".join(self.LAYERS),
            "--no-protel-ext",
            str(self.pcb_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                self._log(f"ERROR: kicad-cli returned {result.returncode}")
                if result.stderr:
                    self._log(result.stderr.strip())
                return []

            exported = (list(self.output_dir.glob("*.gbr")) +
                        list(self.output_dir.glob("*.gtl")) +
                        list(self.output_dir.glob("*.gbl")) +
                        list(self.output_dir.glob("*.drl")))
            self._log(f"  Exported {len(exported)} files")
            return exported

        except subprocess.TimeoutExpired:
            self._log("ERROR: kicad-cli timed out after 120 s")
            return []
        except FileNotFoundError:
            self._log(f"ERROR: kicad-cli not found at: {self.kicad_cli}")
            return []
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            return []
