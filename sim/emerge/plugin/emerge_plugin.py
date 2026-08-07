"""
EMerge FEM Simulation Plugin — KiCad PCB Action Plugin
Launches the EMerge FEM pipeline from the PCB editor toolbar.

Architecture
------------
KiCad embeds Python 3.11, but EMerge requires the system Python (3.12+).
This plugin handles the split cleanly:

  KiCad Python (this file)
    1. Read port definitions from emerge_config.toml
    2. Export Gerbers via kicad-cli (subprocess, no pcbnew export needed)
    3. Write a job.json describing the simulation
    4. Spawn the system Python with emerge_runner.py --job job.json
    5. Poll for job.result.json
    6. Display result dialog

  System Python (emerge_runner.py __main__)
    - Build PCBNew model from stackup + pad positions
    - Run frequency sweep
    - Write Touchstone + per-simulation log

If emerge is found in KiCad's own Python (e.g. pip-installed there), the
subprocess bridge is skipped and emerge_runner is imported directly.

Author: Author
Version: 1.2.0
"""

import json
import os
import pathlib
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

import pcbnew
import wx

# ── TOML loader ───────────────────────────────────────────────────────────────
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        try:
            import toml as tomllib
        except ImportError:
            tomllib = None

# ── Plugin directory — all sibling modules live here ─────────────────────────
_PLUGIN_DIR = pathlib.Path(__file__).parent.resolve()


# =========================================================================== #
# Python / emerge detection
# =========================================================================== #

def _find_emerge_python() -> str | None:
    """
    Return the path to the Python interpreter that has emerge installed.

    Search order:
      1. EMERGE_PYTHON environment variable
      2. %LOCALAPPDATA%\\Programs\\Python\\Python3*\\python.exe  (newest first)
      3. PATH: python3.12, python3.11, python3.10, python3, python
      4. Common system-wide paths: C:\\Python3*, C:\\Program Files\\Python*
      5. KiCad's own Python — only if sys.executable is actually python*.exe
    """
    import shutil as _shutil

    candidates = []

    # 1. Explicit env override — takes priority over everything
    override = os.environ.get("EMERGE_PYTHON", "").strip()
    if override:
        p = pathlib.Path(override)
        if p.exists() and _test_emerge(str(p)):
            return str(p)

    # 2. Windows AppData user install (typical developer setup)
    appdata = os.environ.get("LOCALAPPDATA", "")
    if appdata:
        py_base = pathlib.Path(appdata) / "Programs" / "Python"
        if py_base.is_dir():
            for sub in sorted(py_base.iterdir(), reverse=True):
                exe = sub / "python.exe"
                if exe.is_file():
                    candidates.append(str(exe))

    # 3. PATH
    for name in ("python3.12", "python3.11", "python3.10", "python3", "python"):
        found = _shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)

    # 4. Common system-wide install roots
    for root_str in (r"C:\Python3", r"C:\Program Files\Python"):
        root = pathlib.Path(root_str).parent
        prefix = pathlib.Path(root_str).name
        if root.is_dir():
            for sub in sorted(root.glob(f"{prefix}*"), reverse=True):
                exe = sub / "python.exe"
                if exe.is_file() and str(exe) not in candidates:
                    candidates.append(str(exe))

    # Skip KiCad's own executable — kicad.exe exits 0 on any -c arg but is
    # not a Python interpreter.  Only accept candidates that are python*.exe.
    kicad_exe = pathlib.Path(sys.executable).resolve()
    for exe in candidates:
        p = pathlib.Path(exe)
        if p.resolve() == kicad_exe:
            continue
        if not p.stem.lower().startswith("python"):
            continue
        if _test_emerge(str(p)):
            return str(p)

    # 5. KiCad's own Python — only when sys.executable really is python*.exe
    if pathlib.Path(sys.executable).stem.lower().startswith("python"):
        if _test_emerge(sys.executable):
            return sys.executable

    return None


def _test_emerge(python_exe: str) -> bool:
    """Return True if this interpreter has emerge importable."""
    try:
        r = subprocess.run(
            [python_exe, "-c", "import emerge; print(emerge.__version__)"],
            capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _emerge_version(python_exe: str) -> str:
    # Redirect stdout→stderr during emerge import so the ANSI INFO line
    # does not pollute stdout; only the plain version number is printed.
    _cmd = (
        "import sys; _s=sys.stdout; sys.stdout=sys.stderr; "
        "import emerge; sys.stdout=_s; print(emerge.__version__)"
    )
    try:
        r = subprocess.run(
            [python_exe, "-c", _cmd],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace")
        ver = r.stdout.strip()
        return ver if ver else "?"
    except Exception:
        return "?"


# =========================================================================== #
# Configuration
# =========================================================================== #

def _load_config(config_path: pathlib.Path) -> dict:
    if tomllib is None or not config_path.exists():
        return {}
    try:
        with open(config_path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:
        print(f"WARNING: Cannot parse {config_path}: {exc}")
        return {}


def _build_port_defs(config: dict) -> dict:
    ports_cfg = config.get("ports", {})
    if not ports_cfg:
        return {}
    port_defs = {}
    for name, cfg in ports_cfg.items():
        c_val = cfg.get("C", 0)
        port_defs[name.upper()] = {
            "pad":    cfg.get("pad", ""),
            "R":      float(cfg.get("R", 50.0)),
            "C":      float(c_val) if c_val else None,
            "active": bool(cfg.get("active", True)),
            "dir":    str(cfg.get("dir", "z")),
        }
    return port_defs


# =========================================================================== #
# Dialogs
# =========================================================================== #

class EmergeSetupDialog(wx.Dialog):
    """
    Shown when emerge is NOT found — offers install options and manual path entry.
    """

    def __init__(self, parent):
        wx.Dialog.__init__(self, parent, -1, "EMerge — Python Setup",
                           size=(520, 320))
        sizer = wx.BoxSizer(wx.VERTICAL)

        msg = (
            "EMerge is not found in any Python interpreter.\n\n"
            "Options:\n"
            "  1. Install EMerge into the system Python:\n"
            "       pip install emerge\n\n"
            "  2. Set the EMERGE_PYTHON environment variable to the\n"
            "     full path of a Python executable that has emerge.\n\n"
            "  3. Install emerge into KiCad's own Python:\n"
            "       \"C:\\Program Files\\KiCad\\9.0\\bin\\python.exe\" -m pip install emerge\n\n"
            "After installing, restart KiCad and refresh plugins."
        )
        sizer.Add(wx.StaticText(self, -1, msg), 1, wx.ALL | wx.EXPAND, 12)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        copy_btn = wx.Button(self, -1, "Copy pip command")
        copy_btn.Bind(wx.EVT_BUTTON, self._copy_pip)
        btn_sizer.Add(copy_btn, 0, wx.ALL, 5)
        btn_sizer.AddStretchSpacer()
        ok_btn = wx.Button(self, wx.ID_OK, "Close")
        ok_btn.SetDefault()
        btn_sizer.Add(ok_btn, 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(sizer)
        self.Centre()

    def _copy_pip(self, event):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject("pip install emerge"))
            wx.TheClipboard.Close()


class EmergeLogDialog(wx.Dialog):
    """
    Live simulation progress dialog.

    Shows a scrolling log of all stdout/stderr from the emerge_runner subprocess
    plus plugin-side messages (Gerber export, command line).  A gauge tracks the
    five pipeline stages.  The Close button is disabled until the simulation
    finishes so the user cannot dismiss it mid-solve.
    """

    # Keywords in output lines → stage index (0-based, max gauge = N_STAGES)
    N_STAGES = 5
    _STAGE_KEYWORDS = [
        (1, ("emerge_runner v", "Stackup:", "FileBasedPCB", "PCBNew",
             "Board region:", "Port ", "Model built")),
        (2, ("Generating mesh",)),
        (3, ("Running FEM sweep",)),
        (4, ("Touchstone written", "S21:", "S11:", "Total violations")),
    ]
    _STAGE_LABELS = {
        0: "Exporting Gerbers …",
        1: "Building FEM model …",
        2: "Mesh generation …",
        3: "Running FEM sweep …",
        4: "Finalizing report …",
    }

    def __init__(self, parent):
        wx.Dialog.__init__(
            self, parent, -1, "EMerge — Simulation Progress",
            size=(740, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self._stage = 0
        self._closed = False

        main = wx.BoxSizer(wx.VERTICAL)

        # ── Stage label + gauge ───────────────────────────────────────────────
        self._label = wx.StaticText(self, -1, "Starting …",
                                    style=wx.ST_ELLIPSIZE_END)
        f = self._label.GetFont()
        f.SetWeight(wx.FONTWEIGHT_BOLD)
        self._label.SetFont(f)
        main.Add(self._label, 0, wx.ALL | wx.EXPAND, 8)

        self._gauge = wx.Gauge(self, -1, range=self.N_STAGES, size=(-1, 14))
        main.Add(self._gauge, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM | wx.EXPAND, 8)

        # ── Log text area ─────────────────────────────────────────────────────
        self._log = wx.TextCtrl(
            self, -1, "",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL | wx.TE_RICH2)
        self._log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                  wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        main.Add(self._log, 1, wx.LEFT | wx.RIGHT | wx.EXPAND, 8)

        # ── Button row ────────────────────────────────────────────────────────
        btns = wx.BoxSizer(wx.HORIZONTAL)
        copy_btn = wx.Button(self, -1, "Copy Log")
        copy_btn.Bind(wx.EVT_BUTTON, self._on_copy)
        btns.Add(copy_btn, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        self._close_btn = wx.Button(self, wx.ID_OK, "Close")
        self._close_btn.Enable(False)
        btns.Add(self._close_btn, 0, wx.ALL, 5)
        main.Add(btns, 0, wx.EXPAND | wx.ALL, 5)

        self.SetSizer(main)
        self.Centre()
        # Prevent accidental close via Alt-F4 while running
        self.Bind(wx.EVT_CLOSE, self._on_close)

    # ── public helpers ────────────────────────────────────────────────────────

    def append(self, line: str):
        """Append one line to the log and auto-advance the stage gauge."""
        self._log.AppendText(line + "\n")
        self._maybe_advance_stage(line)

    def set_stage(self, idx: int, label: str):
        self._stage = max(self._stage, idx)
        self._gauge.SetValue(self._stage)
        self._label.SetLabel(label)

    def finish(self, violations: int, ts_path, report_path):
        """Called when the subprocess exits.  Shows final banner, enables Close."""
        self._gauge.SetValue(self.N_STAGES)
        sep = "─" * 60
        self.append(sep)
        if violations < 0:
            self.append("  SIMULATION FAILED — see log above.")
            self._label.SetLabel("Failed")
            self._label.SetForegroundColour(wx.Colour(200, 0, 0))
        elif violations == 0:
            self.append("  All thresholds PASSED.")
            self._label.SetLabel("Passed — all thresholds met")
            self._label.SetForegroundColour(wx.Colour(0, 130, 0))
        else:
            self.append(f"  {violations} threshold violation(s).")
            self._label.SetLabel(f"Done — {violations} violation(s)")
            self._label.SetForegroundColour(wx.Colour(180, 80, 0))
        if ts_path:
            self.append(f"  Touchstone : {ts_path}")
        self.append(f"  Report     : {report_path}")
        self.append(sep)
        self._close_btn.Enable(True)
        self._close_btn.SetDefault()
        self._close_btn.SetFocus()

    # ── internals ─────────────────────────────────────────────────────────────

    def _maybe_advance_stage(self, line: str):
        # Prefer explicit stage headers when available, e.g.:
        #   Stage 3 / 5 — Mesh generation
        #   Stage 2-5 / 5 — EMerge FEM solver (system Python)
        _m = re.match(r"^\s*Stage\s+(\d+)(?:-\d+)?\s*/\s*\d+\s*[—-]\s*(.+?)\s*$", line)
        if _m:
            _stage_idx = max(0, min(self.N_STAGES - 1, int(_m.group(1)) - 1))
            if _stage_idx > self._stage:
                self._stage = _stage_idx
                self._gauge.SetValue(self._stage)
            _title = _m.group(2).strip()
            if _title:
                self._label.SetLabel(f"Stage {_stage_idx + 1}/{self.N_STAGES} — {_title}")
            return

        for idx, keywords in self._STAGE_KEYWORDS:
            if idx > self._stage and any(k in line for k in keywords):
                self._stage = idx
                self._gauge.SetValue(self._stage)
                self._label.SetLabel(
                    f"Stage {idx + 1}/{self.N_STAGES} — {self._STAGE_LABELS.get(idx, 'Running …')}"
                )
                break

    def _on_copy(self, _event):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(self._log.GetValue()))
            wx.TheClipboard.Close()

    def _on_close(self, event):
        if self._close_btn.IsEnabled():
            self._closed = True
            event.Skip()   # allow default close


def _stdout_reader(stream, out_queue):
    """Background thread: enqueue each stdout line; put None sentinel at EOF."""
    try:
        for raw in stream:
            out_queue.put(raw.rstrip("\r\n"))
    finally:
        out_queue.put(None)


# =========================================================================== #
# Action Plugin
# =========================================================================== #

class EmergePlugin(pcbnew.ActionPlugin):
    """
    KiCad toolbar plugin — launches the EMerge FEM simulation pipeline.

    Gerber export runs in KiCad's Python.
    EMerge model build + FEM solve runs in the system Python via subprocess.
    Results (Touchstone + pass/fail report) are read back into the plugin.
    """

    def defaults(self):
        self.name             = "EMerge FEM Simulation"
        self.category         = "RF Simulation"
        self.description      = (
            "Run EMerge FEM solver on the open PCB: "
            "Gerber export → model build → frequency sweep → Touchstone"
        )
        self.show_toolbar_button = True

        icon_path = _PLUGIN_DIR / "emerge_icon.png"
        if icon_path.exists():
            self.icon_file_name = str(icon_path)

        self._config_path = _PLUGIN_DIR / "emerge_config.toml"
        self._config      = _load_config(self._config_path)

    # ----------------------------------------------------------------------- #

    def Run(self):
        board = pcbnew.GetBoard()
        if not board:
            wx.MessageBox("No PCB open.", "EMerge", wx.OK | wx.ICON_ERROR)
            return

        pcb_path = board.GetFileName()
        if not pcb_path:
            wx.MessageBox(
                "Save the PCB file first before running the simulation.",
                "EMerge", wx.OK | wx.ICON_WARNING)
            return

        pcb_path   = pathlib.Path(pcb_path)
        output_dir = pcb_path.parent.parent / "sim" / "emerge" / "results"
        output_dir.mkdir(parents=True, exist_ok=True)

        parent = pcbnew.GetCurrentFrame() if hasattr(pcbnew, "GetCurrentFrame") \
                 else None

        # ── Step 0: Configuration dialog ─────────────────────────────────────
        # Always show the config editor so the user can review / change settings
        # before committing to a (potentially long) FEM solve.
        from emerge_config_dialog import EmergeConfigDialog
        cfg_dlg = EmergeConfigDialog(parent, self._config_path)
        result  = cfg_dlg.ShowModal()
        # Reload config from whatever file the user saved / opened
        self._config_path = cfg_dlg.current_path
        cfg_dlg.Destroy()

        if result != wx.ID_OK:
            return   # user pressed Cancel

        # Reload parsed config from the (possibly edited/saved) file
        self._config = _load_config(self._config_path)

        # ── Check for emerge Python ──────────────────────────────────────────
        # Config can specify python_exe directly — skips all auto-detection.
        cfg_py = self._config.get("python", {}).get("python_exe", "").strip()
        emerge_py = cfg_py if cfg_py else _find_emerge_python()
        if not emerge_py:
            dlg = EmergeSetupDialog(parent)
            dlg.ShowModal()
            dlg.Destroy()
            return

        emerge_ver = _emerge_version(emerge_py)

        # ── Port definitions ─────────────────────────────────────────────────
        port_defs = _build_port_defs(self._config)
        if not port_defs:
            wx.MessageBox(
                "No ports defined in emerge_config.toml.\n"
                "Add [ports.PORT1] / [ports.PORT2] sections.",
                "EMerge — No Ports", wx.OK | wx.ICON_WARNING)
            return

        # ── Build job.json ───────────────────────────────────────────────────
        sweep        = self._config.get("sweep", {})
        thresholds   = self._config.get("thresholds", {})
        solver_cfg   = self._config.get("solver", {})
        vis_cfg      = self._config.get("visualization", {})
        passives_cfg = self._config.get("passives", {})
        gerber_cfg   = self._config.get("gerber", {})
        mesh_cfg     = self._config.get("mesh", {})
        gerber_dir   = output_dir / "gerbers"

        job = {
            "pcb_path":   str(pcb_path),
            "gerber_dir": str(gerber_dir),
            "output_dir": str(output_dir / "touchstone"),
            "port_defs":  port_defs,
            "sweep": {
                "start_hz":          float(sweep.get("start_hz", 1e6)),
                "stop_hz":           float(sweep.get("stop_hz",  10e9)),
                "steps":             int  (sweep.get("steps",    201)),
                "cells_per_lambda":  int  (sweep.get("cells_per_lambda", 15)),
            },
            "thresholds": {
                "insertion_loss_db": float(thresholds.get("insertion_loss_db", 3.0)),
                "return_loss_db":    float(thresholds.get("return_loss_db",    10.0)),
            },
            "solver": {
                "engine": str(solver_cfg.get("engine", "auto")),
            },
            "visualization": {
                "show_geometry": bool(vis_cfg.get("show_geometry", False)),
                "show_mesh":     bool(vis_cfg.get("show_mesh",     False)),
            },
            "passives": {
                "model_passives": bool(passives_cfg.get("model_passives", True)),
                "skip":           list(passives_cfg.get("skip", [])),
            },
            "gerber": {
                "use_gerbers": bool(gerber_cfg.get("use_gerbers", True)),
            },
            "mesh": {
                "curved_boundary_resolution": int  (mesh_cfg.get("curved_boundary_resolution",    40)),
                "max_mesh_size_mm":           float(mesh_cfg.get("max_mesh_size_mm",               0)),
                "min_mesh_size_mm":           float(mesh_cfg.get("min_mesh_size_mm",               0)),
                "port_focus_only":            bool (mesh_cfg.get("port_focus_only",            False)),
                "domain_margin_mm":           float(mesh_cfg.get("domain_margin_mm",               0)),
                "port_focus_margin_mm":       float(mesh_cfg.get("port_focus_margin_mm",           0)),
                "simplify_geometry":          bool (mesh_cfg.get("simplify_geometry",          False)),
                "simplify_factor":            float(mesh_cfg.get("simplify_factor",              1.0)),
                "pcb_split_z":               bool (mesh_cfg.get("pcb_split_z",                 True)),
                "pcb_merge":                 bool (mesh_cfg.get("pcb_merge",                   True)),
                "gerber_circ_segments":       int  (mesh_cfg.get("gerber_circ_segments",          64)),
                "gerber_min_circ_segments":   int  (mesh_cfg.get("gerber_min_circ_segments",      24)),
                "gerber_res_mm":              float(mesh_cfg.get("gerber_res_mm",               0.05)),
                "gerber_min_segment_um":      float(mesh_cfg.get("gerber_min_segment_um",       0.0)),
                "gerber_drop_zero_segments":  bool (mesh_cfg.get("gerber_drop_zero_segments",  True)),
                "gerber_simplify_regions":    bool (mesh_cfg.get("gerber_simplify_regions",    False)),
                "gerber_region_min_segment_um": float(mesh_cfg.get("gerber_region_min_segment_um", 0.0)),
                "algorithm_2d":               int  (mesh_cfg.get("algorithm_2d",                   6)),
                "algorithm_3d":               int  (mesh_cfg.get("algorithm_3d",                   1)),
                "smoothing":                  int  (mesh_cfg.get("smoothing",                      10)),
                "max_mesh_retries":           int  (mesh_cfg.get("max_mesh_retries",                8)),
                "artifact_threshold_um":      float(mesh_cfg.get("artifact_threshold_um",        50.0)),
                "char_length_max_floor_mm":   float(mesh_cfg.get("char_length_max_floor_mm",     0.15)),
                "char_length_max_ceil_mm":    float(mesh_cfg.get("char_length_max_ceil_mm",      0.50)),
                "char_length_max_factor":     float(mesh_cfg.get("char_length_max_factor",       0.80)),
                "sliver_threshold_mm":        float(mesh_cfg.get("sliver_threshold_mm",          0.10)),
                "mesh_copper_mm":             float(mesh_cfg.get("mesh_copper_mm",               0.20)),
                "mesh_copper_z_mm":           float(mesh_cfg.get("mesh_copper_z_mm",             0.05)),
                "mesh_component_mm":          float(mesh_cfg.get("mesh_component_mm",            0.15)),
                "mesh_substrate_mm":          float(mesh_cfg.get("mesh_substrate_mm",            0.40)),
                "mesh_air_mm":                float(mesh_cfg.get("mesh_air_mm",                  2.50)),
            },
        }

        job_path    = output_dir / "emerge_job.json"
        result_path = output_dir / "emerge_job.result.json"
        report_path = output_dir / "emerge_report.txt"

        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

        # Remove stale result file
        if result_path.exists():
            result_path.unlink()

        # ── Live log dialog (shown for the full run) ──────────────────────────
        log_dlg = EmergeLogDialog(parent)
        log_dlg.Show()

        report_lines = [
            f"EMerge Plugin v1.2.0 — {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"PCB:          {pcb_path}",
            f"emerge:       {emerge_ver}  ({emerge_py})",
            f"KiCad Python: {sys.executable}",
            "",
        ]
        for line in report_lines:
            if line:
                log_dlg.append(line)

        # ── Step 1: Gerber export (KiCad Python) ─────────────────────────────
        log_dlg.set_stage(0, "Exporting Gerbers …")
        log_dlg.append("─" * 60)
        log_dlg.append("Stage 1 / 5 — Gerber export")
        log_dlg.append("─" * 60)
        wx.GetApp().Yield()

        ger_report: list[str] = []
        from gerber_exporter import GerberExporter
        # Prefer kicad-cli from the same KiCad installation that is running now.
        # sys.executable = C:\Program Files\KiCad\10.0\bin\kicad.exe — pass the
        # bin/ directory so _find_kicad_cli() resolves kicad-cli.exe from there.
        # Falls back to newest installed version if the exe isn't found there.
        _kicad_bin_dir = str(pathlib.Path(sys.executable).parent)
        # Build full copper-layer list from the open board so inner layers are
        # exported on 4-layer (and 6-layer, etc.) boards.
        try:
            _board = pcbnew.GetBoard()
            _n_cu = _board.GetCopperLayerCount()
            _copper_layers = ["F.Cu"]
            for _i in range(1, _n_cu - 1):
                _copper_layers.append(f"In{_i}.Cu")
            _copper_layers.append("B.Cu")
        except Exception:
            _copper_layers = None  # GerberExporter will use default ["F.Cu","B.Cu"]
        exporter = GerberExporter(
            pcb_path=pcb_path, output_dir=gerber_dir,
            kicad_cli=_kicad_bin_dir,
            copper_layers=_copper_layers,
            report_lines=ger_report, verbose=True)
        gerber_ok = exporter.run()

        for line in ger_report:
            log_dlg.append(line)
            report_lines.append(line)
        wx.GetApp().Yield()

        if not gerber_ok:
            log_dlg.append("FATAL: Gerber export failed.")
            log_dlg.finish(-1, None, report_path)
            report_lines.append("FATAL: Gerber export failed.")
            report_path.write_text("\n".join(report_lines), encoding="utf-8")
            while log_dlg.IsShown():
                wx.MilliSleep(100)
                wx.GetApp().Yield()
            log_dlg.Destroy()
            return

        report_lines.append(f"Gerbers: {gerber_dir}")

        # ── Step 2–5: EMerge solve (system Python subprocess) ─────────────────
        log_dlg.set_stage(1, "Building FEM model …")
        runner_path = _PLUGIN_DIR / "emerge_runner.py"

        debug_mode = bool(self._config.get("debug", False)) or \
                     os.environ.get("EMERGE_DEBUG", "0") not in ("0", "")
        cmd = [emerge_py, str(runner_path), "--job", str(job_path)]
        if debug_mode:
            cmd.append("--debug")

        log_dlg.append("")
        log_dlg.append("─" * 60)
        log_dlg.append("Stage 2-5 / 5 — EMerge FEM solver (system Python)")
        log_dlg.append("─" * 60)
        log_dlg.append(f"  Debug  : {'ON' if debug_mode else 'off'}")
        log_dlg.append(f"  Cmd    : {' '.join(cmd)}")
        log_dlg.append("")
        wx.GetApp().Yield()

        report_lines.append(f"Debug:  {'ON' if debug_mode else 'off'}")
        report_lines.append(f"Solver: {' '.join(cmd)}")

        try:
            env = os.environ.copy()
            env["PYTHONUTF8"]        = "1"
            env["PYTHONIOENCODING"]  = "utf-8:replace"
            env["PYTHONUNBUFFERED"]  = "1"   # line-buffered subprocess stdout
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=env,
            )
        except FileNotFoundError:
            log_dlg.append(f"FATAL: Python not found: {emerge_py}")
            log_dlg.finish(-1, None, report_path)
            report_lines.append(f"FATAL: Python not found: {emerge_py}")
            report_path.write_text("\n".join(report_lines), encoding="utf-8")
            while log_dlg.IsShown():
                wx.MilliSleep(100)
                wx.GetApp().Yield()
            log_dlg.Destroy()
            return

        # Reader thread: puts stdout lines into queue; None sentinel at EOF
        stdout_q: queue.Queue = queue.Queue()
        threading.Thread(
            target=_stdout_reader, args=(proc.stdout, stdout_q),
            daemon=True).start()

        # Poll loop: drain queue → log dialog + report_lines
        stream_done = False
        while not stream_done:
            while True:
                try:
                    item = stdout_q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    stream_done = True
                    break
                log_dlg.append(item)
                report_lines.append(f"  solver: {item}")

            if proc.poll() is not None and stdout_q.empty():
                stream_done = True

            wx.MilliSleep(80)
            wx.GetApp().Yield()

        proc.stdout.close()

        # ── Read result JSON ──────────────────────────────────────────────────
        ts_path    = None
        violations = -1

        if result_path.exists():
            try:
                res = json.loads(result_path.read_text(encoding="utf-8"))
                ts_path    = res.get("ts_path")
                violations = int(res.get("violations", -1))
            except Exception as exc:
                report_lines.append(f"FATAL: Cannot read result: {exc}")
        else:
            report_lines.append("FATAL: No result file produced by solver.")

        report_path.write_text("\n".join(report_lines), encoding="utf-8")

        # ── Show final summary inside the log dialog, wait for user to close ──
        log_dlg.finish(violations, ts_path, report_path)
        while log_dlg.IsShown():
            wx.MilliSleep(100)
            wx.GetApp().Yield()
        log_dlg.Destroy()


# ── Register ──────────────────────────────────────────────────────────────────
EmergePlugin().register()
