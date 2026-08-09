"""
passive_modeler.py — R/L/C lumped element modeler for EMerge FEM pipelines.

Extracted from emerge_runner.py so the parsing + geometry insertion logic
can be tested and reused independently of the full pipeline.

Public API
----------
parse_component_value(ref, value_str)
    Convert a KiCad reference + value string to (type_char, SI_float).
    Returns None for DNP / NC / 0R / unparseable values.

PassiveElementModeler
    Reads R/L/C components from a .kicad_pcb file and inserts lumped
    element geometry into a PCBNew / FileBasedPCB model.
"""

import cmath
import math
import pathlib
import inspect


# =============================================================================
# SI prefix table
# =============================================================================

_SI = {
    'T': 1e12, 'G': 1e9,  'M': 1e6,
    'k': 1e3,  'K': 1e3,
    'm': 1e-3,
    'u': 1e-6, 'n': 1e-9, 'p': 1e-12, 'f': 1e-15,
}


# =============================================================================
# Component value parser
# =============================================================================

def parse_component_value(ref: str, value_str: str):
    """
    Convert a KiCad component value string to (type_char, SI_float).

    Type is inferred from the reference prefix:
        R* → resistance [Ω]
        C* → capacitance [F]
        L* → inductance [H]

    Handles EIA notation: "100", "4.7k", "4k7", "10n", "2p2", "4R7",
                          "100nH", "4.7pF", "1MΩ", "0R47"

    Returns None for DNP / NC / unparseable values.
    Skip thresholds: R=0 (zero-ohm jumper), R>10 MΩ (open),
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

    # Strip voltage/current rating suffix: "10uF/16V" → "10uF"
    s = _re.sub(r'[/\\][^/\\]*$', '', s).strip()

    # Descriptive value strings are common in BOM-style footprints,
    # e.g. "10k NTC Thermistor". Keep only the first numeric-like token.
    if " " in s:
        parts = s.split()
        token = next((p for p in parts if _re.search(r'\d', p)), None)
        if token:
            s = token

    # Strip trailing explicit units (H, F, Ohm, Hz) — keep SI prefix
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
        if prefix == "R" and (val == 0.0 or val > 10e6):
            return None
        if prefix == "C" and val < 1e-17:
            return None
        if prefix == "L" and val < 1e-13:
            return None
        return (prefix, val)

    return None


# =============================================================================
# Passive element modeler
# =============================================================================

class PassiveElementModeler:
    """
    Identify R/L/C components from a .kicad_pcb file, parse their values,
    and insert lumped element geometry into a PCBNew / FileBasedPCB model.

    Each component becomes a flat rectangle at the copper-layer Z height,
    assigned an EMerge lumped_element_material carrying the R, L, or C value.
    The current-flow direction is the pad1→pad2 unit vector.

    Works correctly with FileBasedPCB (Gerber-based): the Gerber copper already
    has a physical gap at the component footprint, so the material fills the gap
    and affects S-params.  With PCBNew (solid copper planes), the solid copper
    bypasses the element polygon — use FileBasedPCB for accurate results.

    Args:
        pcb_obj      : PCBNew or FileBasedPCB instance (model under construction).
        pcb_path     : Path to .kicad_pcb (source of component positions/values).
        stackup      : dict from kicad_reader.read_stackup().
        outline_pts  : Board outline point list (mm) — components outside are skipped.
        skip_refs    : Iterable of refdes strings to exclude (e.g. ["C5", "R3"]).
        report_lines : Shared log list.
        verbose      : Print progress to stdout.
    """

    def __init__(self, pcb_obj, pcb_path, stackup,
                 outline_pts=None, skip_refs=None,
                 report_lines=None, verbose=True):
        self.pcb_obj      = pcb_obj
        self.pcb_path     = pathlib.Path(pcb_path)
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
        Add lumped elements to the model.
        Returns the count of elements successfully added.
        """
        try:
            from emerge import lumped_element_material
        except ImportError:
            self._log("  WARNING: lumped_element_material not available in this "
                      "emerge version — passives skipped")
            return 0

        # Keep the PCB extraction/filtering path, but model each component body
        # the same way the stripline testcase does: a direct lumped-element
        # material spanning pad1→pad2 with the parsed R/L/C value.
        try:
            from basicemergesolverhelperpackage.EMergeConstants import (
                series_impedance as _series_impedance,
                parallel_impedance as _parallel_impedance,
            )
            _SERIES_HELPERS = True
        except ImportError:
            _SERIES_HELPERS = False

        try:
            from kicad_reader import read_passive_components, point_in_board
        except ImportError:
            try:
                from .kicad_reader import read_passive_components, point_in_board
            except ImportError:
                self._log("  WARNING: kicad_reader not found — passives skipped")
                return 0

        components = read_passive_components(self.pcb_path)
        if not components:
            self._log("  No R/L/C components found in PCB.")
            return 0

        if _SERIES_HELPERS:
            self._log("  Using direct lumped-element materials (stripline-style insertion).")

        board_t = self.stackup["board_thickness_mm"]  * 1e-3
        cu_t    = self.stackup["copper_thickness_mm"] * 1e-3
        # Use API properties for exact copper-layer Z; fall back to stackup values
        z_top    = getattr(self.pcb_obj, "top",    board_t)
        z_bottom = getattr(self.pcb_obj, "bottom", cu_t)

        added = skipped = 0
        path_model_used = False
        self._log(f"  Passive components in PCB: {len(components)}")

        try:
            _pcb_new_sig = inspect.signature(self.pcb_obj.new)
            _pcb_compile_paths = hasattr(self.pcb_obj, "compile_paths")
        except Exception:
            _pcb_new_sig = None
            _pcb_compile_paths = False

        for comp in components:
            ref   = comp["ref"]
            value = comp["value"]

            if ref in self.skip_refs:
                self._log(f"    {ref:<8} '{value}'  → skipped (user skip list)")
                skipped += 1
                continue

            parsed = parse_component_value(ref, value)
            if parsed is None:
                self._log(f"    {ref:<8} '{value}'  → skipped (unparseable / DNP / 0R)")
                skipped += 1
                continue

            comp_type, value_si = parsed

            # Parse first, then apply domain filter so descriptive strings
            # (e.g. "10k NTC Thermistor") are still recognized in logs.
            if self.outline_pts:
                mx = (comp["pad1_xy"][0] + comp["pad2_xy"][0]) * 0.5
                my = (comp["pad1_xy"][1] + comp["pad2_xy"][1]) * 0.5
                if not point_in_board(mx, my, self.outline_pts):
                    if comp_type == "R":
                        if value_si >= 1e3:
                            parsed_str = f"{value_si/1e3:.3g} kΩ"
                        else:
                            parsed_str = f"{value_si:.3g} Ω"
                    elif comp_type == "C":
                        parsed_str = f"{value_si:.3g} F"
                    else:
                        parsed_str = f"{value_si:.3g} H"
                    self._log(f"    {ref:<8} '{value}'  → skipped (outside board outline, parsed {comp_type} {parsed_str})")
                    skipped += 1
                    continue

            # KiCad reader coordinates are Y-down in mm. Convert to model
            # coordinates (Gerber Y-up) to align with runner ports/bounds.
            x1 = comp["pad1_xy"][0] * 1e-3
            y1 = -comp["pad1_xy"][1] * 1e-3
            x2 = comp["pad2_xy"][0] * 1e-3
            y2 = -comp["pad2_xy"][1] * 1e-3
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            if length < 1e-6:
                self._log(f"    {ref:<8} pads too close ({length*1e3:.4f} mm) — skipped")
                skipped += 1
                continue

            ux, uy =  dx / length,  dy / length
            px, py = -uy,           ux

            pw_m, ph_m = comp["pad_size"][0] * 1e-3, comp["pad_size"][1] * 1e-3
            body_w = (ph_m if abs(ux) >= abs(uy) else pw_m)
            body_w = max(body_w, 0.3e-3)

            pad_len_along = abs(ux) * pw_m + abs(uy) * ph_m
            margin        = pad_len_along * 0.5
            gap_length    = length - 2.0 * margin
            if gap_length > 0.05e-3:
                gx1 = x1 + ux * margin;  gy1 = y1 + uy * margin
                gx2 = x2 - ux * margin;  gy2 = y2 - uy * margin
                body_length = gap_length
            else:
                gx1, gy1, gx2, gy2 = x1, y1, x2, y2
                body_length = length

            layer = comp["layer"]
            z = z_top if ("F.Cu" in layer or layer.upper().startswith("F")) else z_bottom

            hw = body_w * 0.5
            xs = [gx1 - px*hw, gx2 - px*hw, gx2 + px*hw, gx1 + px*hw]
            ys = [gy1 - py*hw, gy2 - py*hw, gy2 + py*hw, gy1 + py*hw]

            area = body_w * cu_t

            if   comp_type == "R":
                val_str = (f"{value_si/1e6:.3g} MΩ" if value_si >= 1e6 else
                           f"{value_si/1e3:.3g} kΩ" if value_si >= 1e3 else
                           f"{value_si:.3g} Ω")
            elif comp_type == "C":
                val_str = (f"{value_si*1e12:.3g} pF" if value_si < 1e-9 else
                           f"{value_si*1e9:.3g} nF"  if value_si < 1e-6 else
                           f"{value_si*1e6:.3g} µF")
            else:
                val_str = (f"{value_si*1e12:.3g} pH" if value_si < 1e-9 else
                           f"{value_si*1e9:.3g} nH"  if value_si < 1e-6 else
                           f"{value_si*1e6:.3g} µH")

            try:
                # Keep model entity names aligned with PCB/schematic refdes.
                mat_name = ref
                dir_vec = (float(ux), float(uy), 0.0)

                # Prefer the stripline-style path API when the PCB object
                # supports it, so the passive is visible as a meshable element.
                if _pcb_new_sig is not None and _pcb_compile_paths:
                    try:
                        if _SERIES_HELPERS:
                            if comp_type == "R":
                                z_func = _series_impedance(R=float(value_si))
                            elif comp_type == "L":
                                z_func = _series_impedance(L=float(value_si))
                            else:
                                z_func = _parallel_impedance(C=float(value_si))
                        else:
                            if comp_type == "R":
                                z_func = lambda f, _r=float(value_si): complex(_r)
                            elif comp_type == "L":
                                z_func = lambda f, _l=float(value_si): complex(0.0, 2.0 * math.pi * float(f) * _l)
                            else:
                                z_func = lambda f, _c=float(value_si): complex(1e30) if float(f) == 0 else complex(0.0, -1.0 / (2.0 * math.pi * float(f) * _c))

                        new_kwargs = {}
                        if "z" in _pcb_new_sig.parameters:
                            new_kwargs["z"] = z

                        lead_length = max(0.0, 0.5 * (float(length) - float(body_length)))
                        _le_count_before = len(getattr(self.pcb_obj, "lumped_elements", []) or [])

                        path = self.pcb_obj.new(x1, y1, body_w, (ux, uy), **new_kwargs)
                        path = path.straight(lead_length)
                        path = path.lumped_element(z_func, size=(float(body_length), body_w))
                        path = path.straight(lead_length)

                        # Tag the path object for viewer-friendly naming.
                        for _attr in ("name", "label", "_name"):
                            try:
                                setattr(path, _attr, ref)
                            except Exception:
                                pass

                        # Tag any newly created lumped element(s) with the same refdes.
                        _le_list = getattr(self.pcb_obj, "lumped_elements", None)
                        if isinstance(_le_list, list):
                            for _le in _le_list[_le_count_before:]:
                                for _attr in ("name", "label", "_name"):
                                    try:
                                        setattr(_le, _attr, ref)
                                    except Exception:
                                        pass

                        if hasattr(path, "prio_set"):
                            path.prio_set(0)
                        path_model_used = True
                        layer_s = "top" if z == board_t else "bot"
                        self._log(
                            f"    {ref:<8} {comp_type}  {val_str:<14}  {layer_s}  "
                            f"({x1*1e3:.2f},{y1*1e3:.2f})→({x2*1e3:.2f},{y2*1e3:.2f}) mm  "
                            f"len={body_length*1e3:.2f} mm  lead={lead_length*1e3:.2f} mm  "
                            f"w={body_w*1e3:.2f} mm  [path]")
                        added += 1
                        continue
                    except Exception as exc:
                        self._log(f"    {ref:<8} path model failed: {exc} — falling back to polygon patch")

                # Fallback: direct lumped-element patch geometry.
                # Still uses the parsed PCB footprint geometry, but remains
                # compatible with PCB objects that do not expose the path API.
                kwargs = {comp_type: float(value_si)}
                mat = lumped_element_material(
                    material_name = mat_name,
                    direction     = dir_vec,
                    length        = float(body_length),
                    Area          = float(area),
                    **kwargs,
                )

                self.pcb_obj.add_poly(xs=xs, ys=ys, z=z, material=mat, name=ref)
                layer_s = "top" if z == board_t else "bot"
                self._log(
                    f"    {ref:<8} {comp_type}  {val_str:<14}  {layer_s}  "
                    f"({x1*1e3:.2f},{y1*1e3:.2f})→({x2*1e3:.2f},{y2*1e3:.2f}) mm  "
                    f"len={body_length*1e3:.2f} mm  w={body_w*1e3:.2f} mm")
                added += 1
            except Exception as exc:
                self._log(f"    {ref:<8} passive material build failed: {exc}")
                skipped += 1

        if path_model_used and _pcb_compile_paths:
            try:
                self.pcb_obj.compile_paths(merge=True)
                self._log("  Passive paths compiled with merge=True")
            except Exception as exc:
                self._log(f"  WARNING: passive path compile failed: {exc}")

        self._log(f"  Passives: {added} modeled, {skipped} skipped")
        return added
