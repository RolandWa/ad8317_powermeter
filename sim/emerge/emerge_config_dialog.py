"""
emerge_config_dialog.py — Configuration review/edit dialog for the EMerge plugin.

Shown before every simulation run so the user can verify port definitions,
sweep parameters and thresholds.  Supports:
  • Edit TOML inline
  • Save   — overwrite current file
  • Save As — write to a new path
  • Open   — load a different .toml file
  • Run    — accept current content and proceed
  • Cancel — abort

Author: Author
Version: 1.0.0
"""

import pathlib
import wx

# ── TOML loader / writer (stdlib 3.11+, else tomli read + manual write) ──────
try:
    import tomllib as _tomllib
    import tomllib as _tomllib_read
except ImportError:
    try:
        import tomli as _tomllib_read
    except ImportError:
        _tomllib_read = None

# For writing we always use plain string serialisation — no tomli-w dependency.


def _load_toml(path: pathlib.Path) -> tuple[dict, str]:
    """Return (parsed_dict, raw_text).  raw_text is the file content as-is."""
    raw = path.read_text(encoding="utf-8")
    if _tomllib_read is None:
        return {}, raw
    try:
        data = _tomllib_read.loads(raw)
    except Exception:
        data = {}
    return data, raw


def _save_toml(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# =========================================================================== #
# Config Summary panel — human-readable table of key values
# =========================================================================== #

def _summarise(text: str) -> str:
    """Parse TOML text and build a readable summary string."""
    if _tomllib_read is None:
        return "(TOML parser not available — install tomli)"
    try:
        cfg = _tomllib_read.loads(text)
    except Exception as exc:
        return f"TOML parse error: {exc}"

    lines = []

    # Debug
    debug = cfg.get("debug", False)
    lines.append(f"  Debug mode : {'ON' if debug else 'off'}")
    lines.append("")

    # Ports
    ports = cfg.get("ports", {})
    if ports:
        lines.append(f"  Ports ({len(ports)}):")
        for name, p in ports.items():
            active = "active (source)" if p.get("active", True) else "passive (load)"
            lines.append(f"    {name:<8}  pad={p.get('pad','?'):<10}  "
                         f"R={p.get('R',50)} Ohm  {active}")
    else:
        lines.append("  Ports : (none defined)")
    lines.append("")

    # Sweep
    sw = cfg.get("sweep", {})
    if sw:
        f0  = sw.get("start_hz", 1e6)
        f1  = sw.get("stop_hz",  10e9)
        pts = sw.get("steps", 201)
        cpl = sw.get("cells_per_lambda", 15)
        lines.append(f"  Sweep  : {f0/1e6:.3g} MHz  ->  {f1/1e9:.3g} GHz  "
                     f"({pts} pts,  {cpl} cells/lambda)")
    lines.append("")

    # Thresholds
    th = cfg.get("thresholds", {})
    if th:
        lines.append(f"  IL threshold : {th.get('insertion_loss_db', 3.0)} dB  "
                     f"(|S21| loss limit)")
        lines.append(f"  RL threshold : {th.get('return_loss_db', 10.0)} dB  "
                     f"(|S11| return loss min)")
    lines.append("")

    # Solver
    slv = cfg.get("solver", {})
    engine = slv.get("engine", "auto") or "auto"
    lines.append(f"  Solver     : {engine.upper()}")
    lines.append("")

    # Visualization
    vis = cfg.get("visualization", {})
    show_geo  = vis.get("show_geometry", False)
    show_mesh = vis.get("show_mesh",     False)
    geo_viewer = str(vis.get("geometry_viewer", "auto") or "auto").strip().lower()
    mesh_viewer = str(vis.get("mesh_viewer", "auto") or "auto").strip().lower()
    show_field_anim = bool(vis.get("show_field_animation", False))
    field_component = str(vis.get("field_component", "Ez") or "Ez")
    field_anim_f_hz = float(vis.get("field_animation_freq_hz", 0.0) or 0.0)
    vis_parts = []
    if show_geo:  vis_parts.append("geometry")
    if show_mesh: vis_parts.append("mesh")
    lines.append(f"  3D preview : {', '.join(vis_parts) if vis_parts else 'off'}")
    lines.append(f"    Geometry viewer backend : {geo_viewer}")
    lines.append(f"    Mesh viewer backend     : {mesh_viewer}")
    if show_field_anim:
        if field_anim_f_hz > 0:
            lines.append(
                f"    Field animation         : on ({field_component} @ {field_anim_f_hz/1e9:.6g} GHz)"
            )
        else:
            lines.append(
                f"    Field animation         : on ({field_component} @ mid sweep freq)"
            )
    else:
        lines.append("    Field animation         : off")
    lines.append("")

    # Mesh
    mesh = cfg.get("mesh", {})
    cbr  = mesh.get("curved_boundary_resolution", 200)
    maxs = mesh.get("max_mesh_size_mm", 0)
    mins = mesh.get("min_mesh_size_mm", 0)
    alg2 = mesh.get("algorithm_2d", 6)
    alg3 = mesh.get("algorithm_3d", 1)
    smth = mesh.get("smoothing", 10)
    retr = mesh.get("max_mesh_retries", 8)
    art  = mesh.get("artifact_threshold_um", 50)
    clf  = mesh.get("char_length_max_floor_mm", 0.15)
    clc  = mesh.get("char_length_max_ceil_mm", 0.50)
    clx  = mesh.get("char_length_max_factor", 0.80)
    slv  = mesh.get("sliver_threshold_mm", 0.10)
    mcu  = mesh.get("mesh_copper_mm", 0.20)
    mco  = mesh.get("mesh_component_mm", 0.15)
    msu  = mesh.get("mesh_substrate_mm", 0.40)
    mai  = mesh.get("mesh_air_mm", 2.50)
    _ALG2_NAMES = {1: "MeshAdapt", 5: "Delaunay", 6: "Frontal-Delaunay", 8: "Frontal-quads"}
    _ALG3_NAMES = {1: "Delaunay", 4: "Frontal", 10: "HXT"}
    lines.append(f"  Mesh ── GMSH settings ───────────────────")
    lines.append(f"    Curved boundary res : {cbr}  (arc faceting quality)")
    lines.append(f"    Size limits         : min={mins if mins else 'auto'}  max={maxs if maxs else 'auto'}  mm")
    lines.append(f"    2D algorithm        : {alg2}  ({_ALG2_NAMES.get(alg2, '?')})")
    lines.append(f"    3D algorithm        : {alg3}  ({_ALG3_NAMES.get(alg3, '?')})")
    lines.append(f"    Smoothing passes    : {smth}")
    lines.append(f"    Max mesh retries    : {retr}")
    lines.append(f"  Mesh ── CharacteristicLengthMax ─────────")
    lines.append(f"    Artifact skip       : < {art} µm  (skip degenerate curves)")
    lines.append(f"    CL factor           : {clx}  (min_curve_len × factor)")
    lines.append(f"    CL range            : {clf} – {clc} mm  (floor – ceil)")
    lines.append(f"  Mesh ── Sliver surfaces ─────────────────")
    lines.append(f"    Sliver threshold    : {slv} mm  (bbox_diag < this → sliver)")
    lines.append(f"  Mesh ── Per-region sizes ────────────────")
    lines.append(f"    Copper  (F.Cu/B.Cu) : {mcu} mm")
    lines.append(f"    Components (R/L/C)  : {mco} mm")
    lines.append(f"    Substrate (FR4)     : {msu} mm")
    lines.append(f"    Air / PML           : {mai} mm")
    lines.append("")

    # Gerber / PCB
    ger = cfg.get("gerber", {})
    cli = ger.get("kicad_cli", "") or "(auto-detect)"
    use_gbr = ger.get("use_gerbers", True)
    lines.append(f"  PCB mode   : {'FileBasedPCB (Gerbers, accurate)' if use_gbr else 'PCBNew (simplified, fast)'}")
    lines.append(f"  kicad-cli  : {cli}")
    lines.append("")

    # Passives
    pas = cfg.get("passives", {})
    model_pas = pas.get("model_passives", True)
    skip_pas  = pas.get("skip", [])
    lines.append(f"  Passives   : {'modelled' if model_pas else 'disabled (faster)'}"
                 + (f"  skip={skip_pas}" if skip_pas else ""))
    lines.append("")

    # Python
    py_cfg = cfg.get("python", {})
    py_exe = py_cfg.get("python_exe", "") or "(auto-detect)"
    lines.append(f"  Python     : {py_exe}")

    return "\n".join(lines)


# =========================================================================== #
# EmergeConfigDialog
# =========================================================================== #

class EmergeConfigDialog(wx.Dialog):
    """
    Full-featured TOML config editor dialog.

    Usage:
        dlg = EmergeConfigDialog(parent, config_path)
        if dlg.ShowModal() == wx.ID_OK:
            config_path = dlg.current_path   # may have changed via Save As / Open
            config_text = dlg.get_text()
    """

    TITLE = "EMerge — Simulation Configuration"

    def __init__(self, parent, config_path: pathlib.Path):
        wx.Dialog.__init__(self, parent, -1, self.TITLE,
                           size=(820, 680),
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self.current_path = pathlib.Path(config_path)
        self._dirty = False   # unsaved changes

        self._build_ui()
        self._load_file(self.current_path)
        self.Centre()

    # ----------------------------------------------------------------------- #
    # UI construction
    # ----------------------------------------------------------------------- #

    def _build_ui(self):
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # ── Path bar ─────────────────────────────────────────────────────────
        path_sizer = wx.BoxSizer(wx.HORIZONTAL)
        path_sizer.Add(wx.StaticText(self, -1, "Config file:"), 0,
                       wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self._path_label = wx.StaticText(self, -1, "",
                                         style=wx.ST_ELLIPSIZE_START)
        self._path_label.SetForegroundColour(wx.Colour(60, 100, 180))
        path_sizer.Add(self._path_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        main_sizer.Add(path_sizer, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)

        # ── Splitter: editor left, summary right ──────────────────────────────
        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)

        # Left — TOML editor
        left = wx.Panel(splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        left_sizer.Add(wx.StaticText(left, -1, "TOML configuration:"), 0,
                       wx.LEFT | wx.TOP, 4)
        self._editor = self._make_editor(left)
        left_sizer.Add(self._editor, 1, wx.EXPAND | wx.ALL, 4)
        left.SetSizer(left_sizer)

        # Right — summary
        right = wx.Panel(splitter)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer.Add(wx.StaticText(right, -1, "Simulation summary:"), 0,
                        wx.LEFT | wx.TOP, 4)
        self._summary = wx.TextCtrl(right, -1, "",
                                    style=wx.TE_MULTILINE | wx.TE_READONLY |
                                          wx.HSCROLL | wx.TE_RICH2)
        self._summary.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self._summary.SetBackgroundColour(wx.Colour(245, 248, 255))
        right_sizer.Add(self._summary, 1, wx.EXPAND | wx.ALL, 4)
        right.SetSizer(right_sizer)

        splitter.SplitVertically(left, right, 460)
        splitter.SetMinimumPaneSize(200)
        main_sizer.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        # ── Dirty indicator ───────────────────────────────────────────────────
        self._dirty_label = wx.StaticText(self, -1, "")
        self._dirty_label.SetForegroundColour(wx.Colour(200, 80, 0))
        main_sizer.Add(self._dirty_label, 0, wx.LEFT | wx.TOP, 8)

        # ── Button row ────────────────────────────────────────────────────────
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        open_btn    = wx.Button(self, -1, "Open ...")
        save_btn    = wx.Button(self, -1, "Save")
        saveas_btn  = wx.Button(self, -1, "Save As ...")
        self._run_btn    = wx.Button(self, wx.ID_OK,     "Run Simulation")
        cancel_btn  = wx.Button(self, wx.ID_CANCEL, "Cancel")

        self._run_btn.SetDefault()
        font = self._run_btn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self._run_btn.SetFont(font)
        self._run_btn.SetForegroundColour(wx.Colour(0, 100, 0))

        btn_sizer.Add(open_btn,   0, wx.ALL, 5)
        btn_sizer.Add(save_btn,   0, wx.ALL, 5)
        btn_sizer.Add(saveas_btn, 0, wx.ALL, 5)
        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(cancel_btn,      0, wx.ALL, 5)
        btn_sizer.Add(self._run_btn,   0, wx.ALL, 5)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(main_sizer)

        # ── Event bindings ────────────────────────────────────────────────────
        open_btn.Bind(wx.EVT_BUTTON,   self._on_open)
        save_btn.Bind(wx.EVT_BUTTON,   self._on_save)
        saveas_btn.Bind(wx.EVT_BUTTON, self._on_saveas)
        self._editor.Bind(wx.EVT_TEXT, self._on_edit)
        self.Bind(wx.EVT_CLOSE,        self._on_close)

    def _make_editor(self, parent) -> wx.TextCtrl:
        ctrl = wx.TextCtrl(
            parent, -1, "",
            style=wx.TE_MULTILINE | wx.TE_PROCESS_TAB | wx.HSCROLL | wx.TE_RICH2)
        ctrl.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        return ctrl

    # ----------------------------------------------------------------------- #
    # File I/O
    # ----------------------------------------------------------------------- #

    def _load_file(self, path: pathlib.Path):
        if not path.exists():
            wx.MessageBox(f"Config file not found:\n{path}",
                          "EMerge", wx.OK | wx.ICON_WARNING)
            return
        _, raw = _load_toml(path)
        self._editor.ChangeValue(raw)   # ChangeValue doesn't fire EVT_TEXT
        self.current_path = path
        self._path_label.SetLabel(str(path))
        self._set_dirty(False)
        self._refresh_summary(raw)

    def _refresh_summary(self, text: str):
        self._summary.SetValue(_summarise(text))

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._dirty_label.SetLabel("  * Unsaved changes" if dirty else "")

    # ----------------------------------------------------------------------- #
    # Public API
    # ----------------------------------------------------------------------- #

    def get_text(self) -> str:
        return self._editor.GetValue()

    def get_config(self) -> dict:
        if _tomllib_read is None:
            return {}
        try:
            return _tomllib_read.loads(self.get_text())
        except Exception:
            return {}

    # ----------------------------------------------------------------------- #
    # Button handlers
    # ----------------------------------------------------------------------- #

    def _on_edit(self, event):
        self._set_dirty(True)
        self._refresh_summary(self._editor.GetValue())
        event.Skip()

    def _on_open(self, event):
        if self._dirty and not self._confirm_discard():
            return
        dlg = wx.FileDialog(
            self, "Open EMerge configuration",
            defaultDir=str(self.current_path.parent),
            defaultFile="",
            wildcard="TOML files (*.toml)|*.toml|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self._load_file(pathlib.Path(dlg.GetPath()))
        dlg.Destroy()

    def _on_save(self, event):
        try:
            _save_toml(self.current_path, self.get_text())
            self._set_dirty(False)
            self._path_label.SetLabel(str(self.current_path))
        except Exception as exc:
            wx.MessageBox(f"Save failed:\n{exc}", "EMerge",
                          wx.OK | wx.ICON_ERROR)

    def _on_saveas(self, event):
        dlg = wx.FileDialog(
            self, "Save EMerge configuration as",
            defaultDir=str(self.current_path.parent),
            defaultFile=self.current_path.name,
            wildcard="TOML files (*.toml)|*.toml|All files (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            new_path = pathlib.Path(dlg.GetPath())
            try:
                _save_toml(new_path, self.get_text())
                self.current_path = new_path
                self._path_label.SetLabel(str(new_path))
                self._set_dirty(False)
            except Exception as exc:
                wx.MessageBox(f"Save As failed:\n{exc}", "EMerge",
                              wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    def _on_close(self, event):
        if self._dirty:
            if not self._confirm_discard():
                return
        event.Skip()

    def _confirm_discard(self) -> bool:
        """Ask the user whether to discard unsaved changes. Returns True to proceed."""
        res = wx.MessageBox(
            "You have unsaved changes.\nDiscard them and continue?",
            "EMerge — Unsaved changes",
            wx.YES_NO | wx.ICON_QUESTION)
        return res == wx.YES
