"""
Tabbed configuration dialog for EMerge plugin.

Tabs:
1) Sweep + Run
2) Ports
3) Mesh + Gerber
"""

from __future__ import annotations

import pathlib
import wx
from typing import Callable, Union

try:
    import tomllib as _toml_read
except ImportError:
    try:
        import tomli as _toml_read
    except ImportError:
        _toml_read = None


def _load_toml(path: pathlib.Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    if _toml_read is None:
        return {}, raw
    try:
        return _toml_read.loads(raw), raw
    except Exception:
        return {}, raw


def _toml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_array(values):
    return "[" + ", ".join(_toml_scalar(v) for v in values) + "]"


def _toml_dump(data: dict) -> str:
    lines: list[str] = []

    def emit_table(table: dict, prefix: list[str]):
        simple_keys = [k for k, v in table.items() if not isinstance(v, dict)]
        nested_keys = [k for k, v in table.items() if isinstance(v, dict)]

        if prefix:
            lines.append("[" + ".".join(prefix) + "]")

        for key in simple_keys:
            value = table[key]
            if isinstance(value, list):
                lines.append(f"{key} = {_toml_array(value)}")
            else:
                lines.append(f"{key} = {_toml_scalar(value)}")

        if prefix and (simple_keys or nested_keys):
            lines.append("")

        for key in nested_keys:
            emit_table(table[key], prefix + [key])

    top_simple = {k: v for k, v in data.items() if not isinstance(v, dict)}
    top_nested = {k: v for k, v in data.items() if isinstance(v, dict)}

    for key, value in top_simple.items():
        if isinstance(value, list):
            lines.append(f"{key} = {_toml_array(value)}")
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    if top_simple:
        lines.append("")

    for key, table in top_nested.items():
        emit_table(table, [key])

    text = "\n".join(lines).strip() + "\n"
    return text


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _csv_from_list(values) -> str:
    if not isinstance(values, (list, tuple)):
        return ""
    return ", ".join(str(v) for v in values)


def _parse_csv_list(text: str) -> list[str]:
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


class EmergeConfigDialog(wx.Dialog):
    TITLE = "EMerge - Simulation Configuration"

    def __init__(self, parent, config_path: pathlib.Path):
        wx.Dialog.__init__(
            self,
            parent,
            -1,
            self.TITLE,
            size=(940, 760),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        self.current_path = pathlib.Path(config_path)
        self._cfg: dict = {}
        self._dirty = False
        self._help_timers: list[wx.Timer] = []

        self._build_ui()
        self._load_file(self.current_path)
        self.Centre()

    def _build_ui(self):
        main = wx.BoxSizer(wx.VERTICAL)

        path_row = wx.BoxSizer(wx.HORIZONTAL)
        path_row.Add(wx.StaticText(self, -1, "Config file:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        self._path_label = wx.StaticText(self, -1, "", style=wx.ST_ELLIPSIZE_START)
        self._path_label.SetForegroundColour(wx.Colour(60, 100, 180))
        path_row.Add(self._path_label, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        main.Add(path_row, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)

        self._tabs = wx.Notebook(self)
        self._tab_sweep = self._build_sweep_tab(self._tabs)
        self._tab_ports = self._build_ports_tab(self._tabs)
        self._tab_mesh = self._build_mesh_tab(self._tabs)
        self._tab_gerber = self._build_gerber_tab(self._tabs)

        self._tabs.AddPage(self._tab_sweep, "Sweep + Run", select=True)
        self._tabs.AddPage(self._tab_ports, "Ports")
        self._tabs.AddPage(self._tab_mesh, "Mesh")
        self._tabs.AddPage(self._tab_gerber, "Gerber")
        main.Add(self._tabs, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        self._dirty_label = wx.StaticText(self, -1, "")
        self._dirty_label.SetForegroundColour(wx.Colour(200, 80, 0))
        main.Add(self._dirty_label, 0, wx.LEFT | wx.TOP, 8)

        self._help_hint_label = wx.StaticText(self, -1, "")
        self._help_hint_label.SetForegroundColour(wx.Colour(70, 70, 70))
        main.Add(self._help_hint_label, 0, wx.LEFT | wx.TOP | wx.BOTTOM, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        open_btn = wx.Button(self, -1, "Open ...")
        save_btn = wx.Button(self, -1, "Save")
        saveas_btn = wx.Button(self, -1, "Save As ...")
        self._run_btn = wx.Button(self, wx.ID_OK, "Run Simulation")
        cancel_btn = wx.Button(self, wx.ID_CANCEL, "Cancel")

        self._run_btn.SetDefault()
        font = self._run_btn.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self._run_btn.SetFont(font)
        self._run_btn.SetForegroundColour(wx.Colour(0, 100, 0))

        btns.Add(open_btn, 0, wx.ALL, 5)
        btns.Add(save_btn, 0, wx.ALL, 5)
        btns.Add(saveas_btn, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        btns.Add(cancel_btn, 0, wx.ALL, 5)
        btns.Add(self._run_btn, 0, wx.ALL, 5)
        main.Add(btns, 0, wx.EXPAND | wx.ALL, 5)

        self.SetSizer(main)

        open_btn.Bind(wx.EVT_BUTTON, self._on_open)
        save_btn.Bind(wx.EVT_BUTTON, self._on_save)
        saveas_btn.Bind(wx.EVT_BUTTON, self._on_saveas)
        self._run_btn.Bind(wx.EVT_BUTTON, self._on_run)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build_sweep_tab(self, parent):
        scroll = wx.ScrolledWindow(parent, style=wx.VSCROLL)
        scroll.SetScrollRate(8, 8)
        panel = wx.Panel(scroll)
        sizer = wx.BoxSizer(wx.VERTICAL)

        grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        grid.AddGrowableCol(1, 1)

        self._debug = wx.CheckBox(panel, -1, "Enable debug logs")
        self._start_hz = wx.TextCtrl(panel, -1, "")
        self._stop_hz = wx.TextCtrl(panel, -1, "")
        self._steps = wx.SpinCtrl(panel, -1, min=1, max=50001, initial=10)
        self._sweep_spacing = wx.Choice(panel, -1, choices=["linear", "log"])
        self._cells_per_lambda = wx.SpinCtrl(panel, -1, min=1, max=200, initial=5)

        self._solver_engine = wx.Choice(panel, -1, choices=["auto", "pardiso", "mumps", "cuda", "superlu", "umfpack"])
        self._show_geometry = wx.CheckBox(panel, -1, "Show geometry viewer")
        self._show_mesh = wx.CheckBox(panel, -1, "Show mesh viewer")
        self._geometry_viewer = wx.Choice(panel, -1, choices=["auto", "gmsh", "emerge", "both"])
        self._mesh_viewer = wx.Choice(panel, -1, choices=["auto", "gmsh", "emerge", "both"])
        self._show_field_animation = wx.CheckBox(panel, -1, "Show field animation after sweep")
        self._field_component = wx.TextCtrl(panel, -1, "Ez")
        self._field_animation_freq_hz = wx.TextCtrl(panel, -1, "0")
        self._export_sparam_png = wx.CheckBox(panel, -1, "Export S-parameter PNG")
        self._export_field_html = wx.CheckBox(panel, -1, "Export field HTML view")
        self._insertion_loss_db = wx.SpinCtrlDouble(panel, -1, min=0.0, max=200.0, initial=3.0, inc=0.1)
        self._return_loss_db = wx.SpinCtrlDouble(panel, -1, min=0.0, max=200.0, initial=10.0, inc=0.1)

        self._add_row(grid, panel, "Debug", self._debug, "Global debug switch. Long press for help.")
        self._add_row(
            grid,
            panel,
            "Start frequency (Hz)",
            self._start_hz,
            lambda: self._frequency_help_text("Start"),
        )
        self._add_row(
            grid,
            panel,
            "Stop frequency (Hz)",
            self._stop_hz,
            lambda: self._frequency_help_text("Stop"),
        )
        self._add_row(grid, panel, "Simulation steps", self._steps, "Number of frequency points in the sweep.")
        self._add_row(grid, panel, "Sweep spacing", self._sweep_spacing, "Frequency spacing between points: linear or logarithmic.")
        self._add_row(grid, panel, "cells_per_lambda", self._cells_per_lambda, "Mesh density target used by EMerge.")
        self._add_row(grid, panel, "Insertion loss threshold (dB)", self._insertion_loss_db, "FAIL if worst |S21| loss exceeds this threshold.")
        self._add_row(grid, panel, "Return loss threshold (dB)", self._return_loss_db, "FAIL if best |S11| return loss is below this threshold.")
        self._add_row(grid, panel, "Solver engine", self._solver_engine, "Linear solver backend. List is intentionally limited.")
        self._add_row(grid, panel, "Visualization: geometry", self._show_geometry, "Open geometry viewer before solve.")
        self._add_row(grid, panel, "Visualization: mesh", self._show_mesh, "Open mesh viewer before solve.")
        self._add_row(grid, panel, "Geometry viewer backend", self._geometry_viewer, "Viewer backend choice. Limited values only.")
        self._add_row(grid, panel, "Mesh viewer backend", self._mesh_viewer, "Viewer backend choice. Limited values only.")
        self._add_row(grid, panel, "Field animation", self._show_field_animation, "Open animated field viewer after the sweep completes.")
        self._add_row(grid, panel, "Field component", self._field_component, "Field scalar/component for animation and HTML export, for example Ez, Ex, Ey.")
        self._add_row(grid, panel, "Field animation freq (Hz)", self._field_animation_freq_hz, "0 uses the middle sweep frequency; otherwise use the nearest sweep frequency.")
        self._add_row(grid, panel, "Export S-parameter PNG", self._export_sparam_png, "Write S_params.png next to the Touchstone result.")
        self._add_row(grid, panel, "Export field HTML", self._export_field_html, "Write an interactive field cutplane HTML view next to the Touchstone result.")

        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        sizer.AddStretchSpacer()
        panel.SetSizer(sizer)
        wrapper = wx.BoxSizer(wx.VERTICAL)
        wrapper.Add(panel, 1, wx.EXPAND)
        scroll.SetSizer(wrapper)
        scroll.Layout()
        scroll.FitInside()
        return scroll

    def _build_ports_tab(self, parent):
        panel = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)

        ports_help = (
            "Expected port syntax (TOML):\n"
            "[ports.PORT1]\n"
            "pad = \"J1:1\"\n"
            "R = 50.0\n"
            "active = true\n"
            "dir = \"z\"\n\n"
            "Optional fields:\n"
            "C = 2e-12\n"
            "L = 1e-9\n\n"
            "Add another port as a new section, for example:\n"
            "[ports.PORT4]\n"
            "pad = \"U3:5\"\n"
            "R = 75.0\n"
            "active = false\n"
            "dir = \"x\""
        )

        info = wx.StaticText(
            panel,
            -1,
            "Edit ports in TOML format. Add new blocks like [ports.PORT4]. Hold this text for help.",
        )
        self._bind_hold_help(info, ports_help)
        info.SetToolTip(ports_help)
        sizer.Add(info, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self._ports_text = wx.TextCtrl(
            panel,
            -1,
            "",
            style=wx.TE_MULTILINE | wx.HSCROLL | wx.TE_RICH2,
        )
        font = wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self._ports_text.SetFont(font)
        self._bind_hold_help(self._ports_text, ports_help)
        self._ports_text.SetToolTip(ports_help)
        sizer.Add(self._ports_text, 1, wx.EXPAND | wx.ALL, 10)

        passives_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Passive Modeling")
        self._model_passives = wx.CheckBox(panel, -1, "Model passive R/L/C components")
        self._skip_passives = wx.TextCtrl(panel, -1, "")
        passives_grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        passives_grid.AddGrowableCol(1, 1)
        self._add_row(passives_grid, panel, "Enable passive modeling", self._model_passives, "Automatically insert passive R/L/C models from the PCB into the FEM model.")
        self._add_row(passives_grid, panel, "Skip refs", self._skip_passives, "Comma-separated references to skip, for example: C5, C6, R10")
        passives_box.Add(passives_grid, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(passives_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        self._ports_text.Bind(wx.EVT_TEXT, self._on_any_edit)
        return panel

    def _build_mesh_tab(self, parent):
        scroll = wx.ScrolledWindow(parent, style=wx.VSCROLL)
        scroll.SetScrollRate(8, 8)
        panel = wx.Panel(scroll)

        root = wx.BoxSizer(wx.VERTICAL)
        grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        grid.AddGrowableCol(1, 1)

        self._port_focus_only = wx.CheckBox(panel, -1, "Use port-focused domain only")
        self._domain_margin_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=4.0, inc=0.1)
        self._port_focus_margin_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=2.0, inc=0.1)
        self._curved_boundary_resolution = wx.SpinCtrl(panel, -1, min=1, max=1000, initial=20)
        self._max_mesh_size_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=50.0, initial=0.0, inc=0.05)
        self._min_mesh_size_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=50.0, initial=0.0, inc=0.05)
        self._algorithm_2d = wx.Choice(panel, -1, choices=["1", "5", "6", "8"])
        self._algorithm_3d = wx.Choice(panel, -1, choices=["1", "4", "10"])
        self._smoothing = wx.SpinCtrl(panel, -1, min=0, max=100, initial=10)
        self._max_mesh_retries = wx.SpinCtrl(panel, -1, min=0, max=20, initial=2)
        self._simplify_geometry = wx.CheckBox(panel, -1, "Simplify geometry")
        self._simplify_factor = wx.SpinCtrlDouble(panel, -1, min=1.0, max=10.0, initial=2.5, inc=0.1)
        self._pcb_split_z = wx.CheckBox(panel, -1, "Split PCB in Z")
        self._pcb_merge = wx.CheckBox(panel, -1, "Merge touching PCB solids")
        self._artifact_threshold_um = wx.SpinCtrlDouble(panel, -1, min=0.0, max=10000.0, initial=200.0, inc=1.0)
        self._char_length_max_factor = wx.SpinCtrlDouble(panel, -1, min=0.01, max=100.0, initial=2.5, inc=0.05)
        self._char_length_max_floor_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=0.8, inc=0.05)
        self._char_length_max_ceil_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=8.0, inc=0.1)
        self._sliver_threshold_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=10.0, initial=0.1, inc=0.01)
        self._mesh_copper_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=0.6, inc=0.05)
        self._mesh_copper_z_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=0.35, inc=0.05)
        self._mesh_component_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=0.9, inc=0.05)
        self._mesh_substrate_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=100.0, initial=2.0, inc=0.1)
        self._mesh_air_mm = wx.SpinCtrlDouble(panel, -1, min=0.0, max=200.0, initial=10.0, inc=0.5)

        self._add_row(grid, panel, "Port focus only", self._port_focus_only, "Ignore keepout/outline and crop by ports.")
        self._add_row(grid, panel, "Domain margin (mm)", self._domain_margin_mm, "Global expansion around the selected domain.")
        self._add_row(grid, panel, "Port focus margin (mm)", self._port_focus_margin_mm, "Initial local expansion around ports.")
        self._add_row(grid, panel, "Curved boundary resolution", self._curved_boundary_resolution, "Arc faceting density.")
        self._add_row(grid, panel, "Max mesh size (mm)", self._max_mesh_size_mm, "0 means auto.")
        self._add_row(grid, panel, "Min mesh size (mm)", self._min_mesh_size_mm, "0 means auto.")
        self._add_row(grid, panel, "2D algorithm", self._algorithm_2d, "Allowed values: 1, 5, 6, 8.")
        self._add_row(grid, panel, "3D algorithm", self._algorithm_3d, "Allowed values: 1, 4, 10.")
        self._add_row(grid, panel, "Smoothing", self._smoothing, "Laplacian smoothing passes after mesh generation.")
        self._add_row(grid, panel, "Max mesh retries", self._max_mesh_retries, "Retries for edge-recovery fixes before fallback.")
        self._add_row(grid, panel, "Simplify geometry", self._simplify_geometry, "Pre-import simplification switch.")
        self._add_row(grid, panel, "Simplify factor", self._simplify_factor, "Higher value gives coarser simplified geometry.")
        self._add_row(grid, panel, "PCB split Z", self._pcb_split_z, "Split solids at Z interfaces before commit.")
        self._add_row(grid, panel, "PCB merge", self._pcb_merge, "Merge touching solids before commit.")
        self._add_row(grid, panel, "Artifact threshold (um)", self._artifact_threshold_um, "Boundary curves shorter than this are ignored in CLmax scan.")
        self._add_row(grid, panel, "CL max factor", self._char_length_max_factor, "Multiplier applied to shortest valid large-surface boundary curve.")
        self._add_row(grid, panel, "CL max floor (mm)", self._char_length_max_floor_mm, "Lower clamp for global CharacteristicLengthMax.")
        self._add_row(grid, panel, "CL max ceil (mm)", self._char_length_max_ceil_mm, "Upper clamp for global CharacteristicLengthMax.")
        self._add_row(grid, panel, "Sliver threshold (mm)", self._sliver_threshold_mm, "BBox threshold used to classify sliver surfaces.")
        self._add_row(grid, panel, "Mesh copper (mm)", self._mesh_copper_mm, "Per-region copper mesh target.")
        self._add_row(grid, panel, "Mesh copper Z (mm)", self._mesh_copper_z_mm, "Through-thickness copper Z refinement target.")
        self._add_row(grid, panel, "Mesh component (mm)", self._mesh_component_mm, "Per-region component mesh target.")
        self._add_row(grid, panel, "Mesh substrate (mm)", self._mesh_substrate_mm, "Per-region substrate mesh target.")
        self._add_row(grid, panel, "Mesh air (mm)", self._mesh_air_mm, "Per-region air/PML mesh target.")

        root.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(root)

        wrapper = wx.BoxSizer(wx.VERTICAL)
        wrapper.Add(panel, 1, wx.EXPAND)
        scroll.SetSizer(wrapper)
        scroll.Layout()
        scroll.FitInside()

        return scroll

    def _build_gerber_tab(self, parent):
        scroll = wx.ScrolledWindow(parent, style=wx.VSCROLL)
        scroll.SetScrollRate(8, 8)
        panel = wx.Panel(scroll)
        root = wx.BoxSizer(wx.VERTICAL)

        info = wx.StaticText(
            panel,
            -1,
            "Gerber loading and sanitization controls used by the importer.",
        )
        root.Add(info, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=8)
        grid.AddGrowableCol(1, 1)

        self._use_gerbers = wx.CheckBox(panel, -1, "Use FileBasedPCB Gerber mode")
        self._kicad_cli = wx.TextCtrl(panel, -1, "")
        self._gerber_res_mm = wx.SpinCtrlDouble(panel, -1, min=0.001, max=10.0, initial=0.2, inc=0.01)
        self._gerber_circ_segments = wx.SpinCtrl(panel, -1, min=3, max=2048, initial=64)
        self._gerber_min_circ_segments = wx.SpinCtrl(panel, -1, min=3, max=2048, initial=6)
        self._gerber_min_segment_um = wx.SpinCtrlDouble(panel, -1, min=0.0, max=10000.0, initial=20.0, inc=1.0)
        self._gerber_drop_zero_segments = wx.CheckBox(panel, -1, "Drop zero-length segments")
        self._gerber_simplify_regions = wx.CheckBox(panel, -1, "Simplify region polygons")
        self._gerber_region_min_segment_um = wx.SpinCtrlDouble(panel, -1, min=0.0, max=10000.0, initial=120.0, inc=1.0)
        self._python_exe = wx.TextCtrl(panel, -1, "")
        self._min_emerge_version = wx.TextCtrl(panel, -1, "")
        self._layers_copper = wx.TextCtrl(panel, -1, "")
        self._layers_mask = wx.TextCtrl(panel, -1, "")
        self._layers_silk = wx.TextCtrl(panel, -1, "")
        self._layers_edge = wx.TextCtrl(panel, -1, "")

        self._add_row(grid, panel, "Gerber loading mode", self._use_gerbers, "If off, use simplified PCBNew path.")
        self._add_row(grid, panel, "kicad-cli path", self._kicad_cli, "Leave empty for auto-detect.")
        self._add_row(grid, panel, "Gerber resolution (mm)", self._gerber_res_mm, "res_mm used in Gerber layer import.")
        self._add_row(grid, panel, "Gerber circ segments", self._gerber_circ_segments, "Line segments used to approximate circles.")
        self._add_row(grid, panel, "Gerber min circ segments", self._gerber_min_circ_segments, "Lower bound after simplification scaling.")
        self._add_row(grid, panel, "Gerber min segment (um)", self._gerber_min_segment_um, "Tiny D01 segment threshold.")
        self._add_row(grid, panel, "Drop zero segments", self._gerber_drop_zero_segments, "Always remove zero-length draws.")
        self._add_row(grid, panel, "Simplify regions", self._gerber_simplify_regions, "Enable in-region vertex filtering.")
        self._add_row(grid, panel, "Region min segment (um)", self._gerber_region_min_segment_um, "In-region tiny edge threshold.")
        self._add_row(grid, panel, "Python executable", self._python_exe, "System Python used to run emerge_runner.py.")
        self._add_row(grid, panel, "Minimum EMerge version", self._min_emerge_version, "Minimum expected EMerge version.")
        self._add_row(grid, panel, "Copper layers", self._layers_copper, "Comma-separated copper layer names for standalone runs.")
        self._add_row(grid, panel, "Mask layers", self._layers_mask, "Comma-separated solder mask layer names.")
        self._add_row(grid, panel, "Silk layers", self._layers_silk, "Comma-separated silkscreen layer names.")
        self._add_row(grid, panel, "Edge layers", self._layers_edge, "Comma-separated board edge layer names.")

        root.Add(grid, 0, wx.EXPAND | wx.ALL, 12)
        root.AddStretchSpacer()
        panel.SetSizer(root)
        wrapper = wx.BoxSizer(wx.VERTICAL)
        wrapper.Add(panel, 1, wx.EXPAND)
        scroll.SetSizer(wrapper)
        scroll.Layout()
        scroll.FitInside()
        return scroll

    def _frequency_help_text(self, focus_label: str) -> str:
        def _parse_hz(raw: str) -> float | None:
            txt = str(raw or "").strip().replace("_", "").replace(",", "")
            try:
                return float(txt)
            except Exception:
                return None

        start_hz = _parse_hz(self._start_hz.GetValue())
        stop_hz = _parse_hz(self._stop_hz.GetValue())

        if start_hz is None and stop_hz is None:
            return (
                "Enter frequencies in Hz. Example: 9000000000 = 9 GHz = 9000 MHz = 9000000 kHz"
            )

        def _fmt(label: str, hz_val: float | None) -> str:
            if hz_val is None:
                return f"{label}: invalid"
            ghz = hz_val / 1e9
            mhz = hz_val / 1e6
            khz = hz_val / 1e3
            return (
                f"{label}: {hz_val:,.0f} Hz | {khz:,.6g} kHz | {mhz:,.6g} MHz | {ghz:,.6g} GHz"
            )

        lines = [f"{focus_label} frequency details"]
        lines.append(_fmt("Start", start_hz))
        lines.append(_fmt("Stop", stop_hz))

        if start_hz is not None and stop_hz is not None:
            span_hz = abs(stop_hz - start_hz)
            lines.append(_fmt("Range", span_hz))

        return " ; ".join(lines)

    def _resolve_help_text(self, help_text: Union[str, Callable[[], str]]) -> str:
        if callable(help_text):
            try:
                return str(help_text())
            except Exception:
                return "Help unavailable."
        return str(help_text)

    def _add_row(self, grid, panel, label_text, ctrl, help_text):
        label = wx.StaticText(panel, -1, label_text)
        self._bind_hold_help(label, help_text)
        self._bind_hold_help(ctrl, help_text)
        tip = self._resolve_help_text(help_text)
        label.SetToolTip(tip)
        ctrl.SetToolTip(tip)

        grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(ctrl, 1, wx.EXPAND)

        if isinstance(ctrl, wx.Choice):
            ctrl.Bind(wx.EVT_CHOICE, self._on_any_edit)
        elif isinstance(ctrl, wx.CheckBox):
            ctrl.Bind(wx.EVT_CHECKBOX, self._on_any_edit)
        else:
            ctrl.Bind(wx.EVT_TEXT, self._on_any_edit)

    def _bind_hold_help(self, widget, help_text):
        timer = wx.Timer(self)
        self._help_timers.append(timer)

        state = {"inside": False}

        def on_enter(event):
            state["inside"] = True
            timer.StartOnce(650)
            event.Skip()

        def on_motion(event):
            if state["inside"]:
                # Pointer moved: restart hover dwell timeout.
                timer.StartOnce(650)
            event.Skip()

        def on_leave(event):
            state["inside"] = False
            if timer.IsRunning():
                timer.Stop()
            self._help_hint_label.SetLabel("")
            event.Skip()

        def on_timer(_event):
            if state["inside"]:
                self._help_hint_label.SetLabel(f"Hint: {self._resolve_help_text(help_text)}")

        widget.Bind(wx.EVT_ENTER_WINDOW, on_enter)
        widget.Bind(wx.EVT_MOTION, on_motion)
        widget.Bind(wx.EVT_LEAVE_WINDOW, on_leave)
        self.Bind(wx.EVT_TIMER, on_timer, timer)

    def _load_file(self, path: pathlib.Path):
        if not path.exists():
            wx.MessageBox(f"Config file not found:\n{path}", "EMerge", wx.OK | wx.ICON_WARNING)
            return

        cfg, _raw = _load_toml(path)
        self._cfg = cfg if isinstance(cfg, dict) else {}
        self.current_path = path
        self._path_label.SetLabel(str(path))
        self._populate_controls_from_cfg()
        self._set_dirty(False)

    def _populate_controls_from_cfg(self):
        cfg = self._cfg
        sweep = cfg.get("sweep", {})
        solver = cfg.get("solver", {})
        thresholds = cfg.get("thresholds", {})
        vis = cfg.get("visualization", {})
        mesh = cfg.get("mesh", {})
        passives = cfg.get("passives", {})
        gerber = cfg.get("gerber", {})
        python_cfg = cfg.get("python", {})
        gerber_layers = gerber.get("layers", {}) if isinstance(gerber, dict) else {}

        self._debug.SetValue(bool(cfg.get("debug", False)))
        self._start_hz.SetValue(str(sweep.get("start_hz", 10000000)))
        self._stop_hz.SetValue(str(sweep.get("stop_hz", 10000000000)))
        self._steps.SetValue(_as_int(sweep.get("steps", 10), 10))
        self._sweep_spacing.SetStringSelection(str(sweep.get("spacing", "log")).lower())
        if self._sweep_spacing.GetSelection() == wx.NOT_FOUND:
            self._sweep_spacing.SetStringSelection("log")
        self._cells_per_lambda.SetValue(_as_int(sweep.get("cells_per_lambda", 5), 5))
        self._insertion_loss_db.SetValue(_as_float(thresholds.get("insertion_loss_db", 3.0), 3.0))
        self._return_loss_db.SetValue(_as_float(thresholds.get("return_loss_db", 10.0), 10.0))

        self._solver_engine.SetStringSelection(str(solver.get("engine", "auto")))
        if self._solver_engine.GetSelection() == wx.NOT_FOUND:
            self._solver_engine.SetStringSelection("auto")

        self._show_geometry.SetValue(bool(vis.get("show_geometry", True)))
        self._show_mesh.SetValue(bool(vis.get("show_mesh", True)))
        self._geometry_viewer.SetStringSelection(str(vis.get("geometry_viewer", "auto")))
        if self._geometry_viewer.GetSelection() == wx.NOT_FOUND:
            self._geometry_viewer.SetStringSelection("auto")
        self._mesh_viewer.SetStringSelection(str(vis.get("mesh_viewer", "auto")))
        if self._mesh_viewer.GetSelection() == wx.NOT_FOUND:
            self._mesh_viewer.SetStringSelection("auto")
        self._show_field_animation.SetValue(bool(vis.get("show_field_animation", False)))
        self._field_component.SetValue(str(vis.get("field_component", "Ez") or "Ez"))
        self._field_animation_freq_hz.SetValue(str(vis.get("field_animation_freq_hz", 0) or 0))
        self._export_sparam_png.SetValue(bool(vis.get("export_sparam_png", False)))
        self._export_field_html.SetValue(bool(vis.get("export_field_html", False)))

        self._port_focus_only.SetValue(bool(mesh.get("port_focus_only", True)))
        self._domain_margin_mm.SetValue(_as_float(mesh.get("domain_margin_mm", 4.0), 4.0))
        self._port_focus_margin_mm.SetValue(_as_float(mesh.get("port_focus_margin_mm", 2.0), 2.0))
        self._curved_boundary_resolution.SetValue(_as_int(mesh.get("curved_boundary_resolution", 20), 20))
        self._max_mesh_size_mm.SetValue(_as_float(mesh.get("max_mesh_size_mm", 0.0), 0.0))
        self._min_mesh_size_mm.SetValue(_as_float(mesh.get("min_mesh_size_mm", 0.0), 0.0))
        self._algorithm_2d.SetStringSelection(str(mesh.get("algorithm_2d", 6)))
        if self._algorithm_2d.GetSelection() == wx.NOT_FOUND:
            self._algorithm_2d.SetStringSelection("6")
        self._algorithm_3d.SetStringSelection(str(mesh.get("algorithm_3d", 10)))
        if self._algorithm_3d.GetSelection() == wx.NOT_FOUND:
            self._algorithm_3d.SetStringSelection("10")
        self._smoothing.SetValue(_as_int(mesh.get("smoothing", 10), 10))
        self._max_mesh_retries.SetValue(_as_int(mesh.get("max_mesh_retries", 2), 2))
        self._simplify_geometry.SetValue(bool(mesh.get("simplify_geometry", True)))
        self._simplify_factor.SetValue(_as_float(mesh.get("simplify_factor", 2.5), 2.5))
        self._pcb_split_z.SetValue(bool(mesh.get("pcb_split_z", False)))
        self._pcb_merge.SetValue(bool(mesh.get("pcb_merge", False)))
        self._artifact_threshold_um.SetValue(_as_float(mesh.get("artifact_threshold_um", 200.0), 200.0))
        self._char_length_max_factor.SetValue(_as_float(mesh.get("char_length_max_factor", 2.5), 2.5))
        self._char_length_max_floor_mm.SetValue(_as_float(mesh.get("char_length_max_floor_mm", 0.8), 0.8))
        self._char_length_max_ceil_mm.SetValue(_as_float(mesh.get("char_length_max_ceil_mm", 8.0), 8.0))
        self._sliver_threshold_mm.SetValue(_as_float(mesh.get("sliver_threshold_mm", 0.10), 0.10))
        self._mesh_copper_mm.SetValue(_as_float(mesh.get("mesh_copper_mm", 0.60), 0.60))
        self._mesh_copper_z_mm.SetValue(_as_float(mesh.get("mesh_copper_z_mm", 0.35), 0.35))
        self._mesh_component_mm.SetValue(_as_float(mesh.get("mesh_component_mm", 0.90), 0.90))
        self._mesh_substrate_mm.SetValue(_as_float(mesh.get("mesh_substrate_mm", 2.0), 2.0))
        self._mesh_air_mm.SetValue(_as_float(mesh.get("mesh_air_mm", 10.0), 10.0))
        self._gerber_res_mm.SetValue(_as_float(mesh.get("gerber_res_mm", 0.2), 0.2))
        self._gerber_circ_segments.SetValue(_as_int(mesh.get("gerber_circ_segments", 64), 64))
        self._gerber_min_circ_segments.SetValue(_as_int(mesh.get("gerber_min_circ_segments", 6), 6))
        self._gerber_min_segment_um.SetValue(_as_float(mesh.get("gerber_min_segment_um", 20.0), 20.0))
        self._gerber_drop_zero_segments.SetValue(bool(mesh.get("gerber_drop_zero_segments", True)))
        self._gerber_simplify_regions.SetValue(bool(mesh.get("gerber_simplify_regions", True)))
        self._gerber_region_min_segment_um.SetValue(_as_float(mesh.get("gerber_region_min_segment_um", 120.0), 120.0))

        self._use_gerbers.SetValue(bool(gerber.get("use_gerbers", True)))
        self._kicad_cli.SetValue(str(gerber.get("kicad_cli", "") or ""))
        self._python_exe.SetValue(str(python_cfg.get("python_exe", "") or ""))
        self._min_emerge_version.SetValue(str(python_cfg.get("min_emerge_version", "") or ""))
        self._layers_copper.SetValue(_csv_from_list(gerber_layers.get("copper", [])))
        self._layers_mask.SetValue(_csv_from_list(gerber_layers.get("mask", [])))
        self._layers_silk.SetValue(_csv_from_list(gerber_layers.get("silk", [])))
        self._layers_edge.SetValue(_csv_from_list(gerber_layers.get("edge", [])))
        self._model_passives.SetValue(bool(passives.get("model_passives", True)))
        self._skip_passives.SetValue(_csv_from_list(passives.get("skip", [])))

        self._ports_text.ChangeValue(self._ports_to_text(cfg.get("ports", {})))

    def _ports_to_text(self, ports: dict) -> str:
        if not isinstance(ports, dict) or not ports:
            return ""

        tmp = {"ports": ports}
        text = _toml_dump(tmp)
        return text.strip() + "\n"

    def _parse_ports_text(self, text: str) -> dict:
        text = text.strip()
        if not text:
            return {}
        if _toml_read is None:
            raise ValueError("No TOML parser available. Install tomli.")
        try:
            parsed = _toml_read.loads(text)
        except Exception as exc:
            raise ValueError(f"Ports TOML parse error: {exc}") from exc

        ports = parsed.get("ports", None)
        if not isinstance(ports, dict):
            raise ValueError("Ports text must contain [ports.NAME] sections.")
        return ports

    def _collect_cfg_from_controls(self) -> dict:
        cfg = dict(self._cfg)
        sweep = dict(cfg.get("sweep", {}))
        solver = dict(cfg.get("solver", {}))
        vis = dict(cfg.get("visualization", {}))
        mesh = dict(cfg.get("mesh", {}))
        gerber = dict(cfg.get("gerber", {}))

        cfg["debug"] = bool(self._debug.GetValue())

        sweep["start_hz"] = int(float(self._start_hz.GetValue().strip()))
        sweep["stop_hz"] = int(float(self._stop_hz.GetValue().strip()))
        sweep["steps"] = int(self._steps.GetValue())
        sweep["spacing"] = self._sweep_spacing.GetStringSelection() or "log"
        sweep["cells_per_lambda"] = int(self._cells_per_lambda.GetValue())
        cfg["sweep"] = sweep

        thresholds = dict(cfg.get("thresholds", {}))
        thresholds["insertion_loss_db"] = float(self._insertion_loss_db.GetValue())
        thresholds["return_loss_db"] = float(self._return_loss_db.GetValue())
        cfg["thresholds"] = thresholds

        solver["engine"] = self._solver_engine.GetStringSelection() or "auto"
        cfg["solver"] = solver

        vis["show_geometry"] = bool(self._show_geometry.GetValue())
        vis["show_mesh"] = bool(self._show_mesh.GetValue())
        vis["geometry_viewer"] = self._geometry_viewer.GetStringSelection() or "auto"
        vis["mesh_viewer"] = self._mesh_viewer.GetStringSelection() or "auto"
        vis["show_field_animation"] = bool(self._show_field_animation.GetValue())
        vis["field_component"] = self._field_component.GetValue().strip() or "Ez"
        vis["field_animation_freq_hz"] = float(self._field_animation_freq_hz.GetValue().strip() or "0")
        vis["export_sparam_png"] = bool(self._export_sparam_png.GetValue())
        vis["export_field_html"] = bool(self._export_field_html.GetValue())
        cfg["visualization"] = vis

        mesh["port_focus_only"] = bool(self._port_focus_only.GetValue())
        mesh["domain_margin_mm"] = float(self._domain_margin_mm.GetValue())
        mesh["port_focus_margin_mm"] = float(self._port_focus_margin_mm.GetValue())
        mesh["curved_boundary_resolution"] = int(self._curved_boundary_resolution.GetValue())
        mesh["max_mesh_size_mm"] = float(self._max_mesh_size_mm.GetValue())
        mesh["min_mesh_size_mm"] = float(self._min_mesh_size_mm.GetValue())
        mesh["algorithm_2d"] = int(self._algorithm_2d.GetStringSelection())
        mesh["algorithm_3d"] = int(self._algorithm_3d.GetStringSelection())
        mesh["smoothing"] = int(self._smoothing.GetValue())
        mesh["max_mesh_retries"] = int(self._max_mesh_retries.GetValue())
        mesh["simplify_geometry"] = bool(self._simplify_geometry.GetValue())
        mesh["simplify_factor"] = float(self._simplify_factor.GetValue())
        mesh["pcb_split_z"] = bool(self._pcb_split_z.GetValue())
        mesh["pcb_merge"] = bool(self._pcb_merge.GetValue())
        mesh["artifact_threshold_um"] = float(self._artifact_threshold_um.GetValue())
        mesh["char_length_max_factor"] = float(self._char_length_max_factor.GetValue())
        mesh["char_length_max_floor_mm"] = float(self._char_length_max_floor_mm.GetValue())
        mesh["char_length_max_ceil_mm"] = float(self._char_length_max_ceil_mm.GetValue())
        mesh["sliver_threshold_mm"] = float(self._sliver_threshold_mm.GetValue())
        mesh["mesh_copper_mm"] = float(self._mesh_copper_mm.GetValue())
        mesh["mesh_copper_z_mm"] = float(self._mesh_copper_z_mm.GetValue())
        mesh["mesh_component_mm"] = float(self._mesh_component_mm.GetValue())
        mesh["mesh_substrate_mm"] = float(self._mesh_substrate_mm.GetValue())
        mesh["mesh_air_mm"] = float(self._mesh_air_mm.GetValue())
        mesh["gerber_res_mm"] = float(self._gerber_res_mm.GetValue())
        mesh["gerber_circ_segments"] = int(self._gerber_circ_segments.GetValue())
        mesh["gerber_min_circ_segments"] = int(self._gerber_min_circ_segments.GetValue())
        mesh["gerber_min_segment_um"] = float(self._gerber_min_segment_um.GetValue())
        mesh["gerber_drop_zero_segments"] = bool(self._gerber_drop_zero_segments.GetValue())
        mesh["gerber_simplify_regions"] = bool(self._gerber_simplify_regions.GetValue())
        mesh["gerber_region_min_segment_um"] = float(self._gerber_region_min_segment_um.GetValue())
        cfg["mesh"] = mesh

        gerber["use_gerbers"] = bool(self._use_gerbers.GetValue())
        gerber["kicad_cli"] = self._kicad_cli.GetValue().strip()
        gerber["layers"] = {
            "copper": _parse_csv_list(self._layers_copper.GetValue()),
            "mask": _parse_csv_list(self._layers_mask.GetValue()),
            "silk": _parse_csv_list(self._layers_silk.GetValue()),
            "edge": _parse_csv_list(self._layers_edge.GetValue()),
        }
        cfg["gerber"] = gerber

        cfg["python"] = {
            "python_exe": self._python_exe.GetValue().strip(),
            "min_emerge_version": self._min_emerge_version.GetValue().strip(),
        }

        cfg["passives"] = {
            "model_passives": bool(self._model_passives.GetValue()),
            "skip": _parse_csv_list(self._skip_passives.GetValue()),
        }

        cfg["ports"] = self._parse_ports_text(self._ports_text.GetValue())
        return cfg

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        self._dirty_label.SetLabel("  * Unsaved changes" if dirty else "")

    def _on_any_edit(self, event):
        self._set_dirty(True)
        event.Skip()

    def _on_open(self, _event):
        if self._dirty and not self._confirm_discard():
            return

        dlg = wx.FileDialog(
            self,
            "Open EMerge configuration",
            defaultDir=str(self.current_path.parent),
            defaultFile="",
            wildcard="TOML files (*.toml)|*.toml|All files (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        if dlg.ShowModal() == wx.ID_OK:
            self._load_file(pathlib.Path(dlg.GetPath()))
        dlg.Destroy()

    def _save_current(self) -> bool:
        try:
            cfg = self._collect_cfg_from_controls()
        except Exception as exc:
            wx.MessageBox(f"Cannot save configuration:\n{exc}", "EMerge", wx.OK | wx.ICON_ERROR)
            return False

        try:
            _save_text = _toml_dump(cfg)
            self.current_path.write_text(_save_text, encoding="utf-8")
            self._cfg = cfg
            self._set_dirty(False)
            self._path_label.SetLabel(str(self.current_path))
            return True
        except Exception as exc:
            wx.MessageBox(f"Save failed:\n{exc}", "EMerge", wx.OK | wx.ICON_ERROR)
            return False

    def _on_save(self, _event):
        self._save_current()

    def _on_saveas(self, _event):
        dlg = wx.FileDialog(
            self,
            "Save EMerge configuration as",
            defaultDir=str(self.current_path.parent),
            defaultFile=self.current_path.name,
            wildcard="TOML files (*.toml)|*.toml|All files (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dlg.ShowModal() == wx.ID_OK:
            new_path = pathlib.Path(dlg.GetPath())
            previous = self.current_path
            self.current_path = new_path
            if not self._save_current():
                self.current_path = previous
        dlg.Destroy()

    def _on_run(self, _event):
        try:
            cfg = self._collect_cfg_from_controls()
            _toml_dump(cfg)
            self._cfg = cfg
            self.EndModal(wx.ID_OK)
        except Exception as exc:
            wx.MessageBox(f"Cannot run with current values:\n{exc}", "EMerge", wx.OK | wx.ICON_ERROR)

    def _on_close(self, event):
        if self._dirty and not self._confirm_discard():
            return
        event.Skip()

    def _confirm_discard(self) -> bool:
        res = wx.MessageBox(
            "You have unsaved changes.\nDiscard them and continue?",
            "EMerge - Unsaved changes",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        return res == wx.YES

    def get_text(self) -> str:
        try:
            cfg = self._collect_cfg_from_controls()
        except Exception:
            cfg = self._cfg
        return _toml_dump(cfg)

    def get_config(self) -> dict:
        try:
            return self._collect_cfg_from_controls()
        except Exception:
            return dict(self._cfg)
