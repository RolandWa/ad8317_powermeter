"""
KiCad PCB Reader — ad8317_powermeter EMerge pipeline
Provides pad-position, stackup, and passive-component extraction from
.kicad_pcb files.

No pcbnew or KiCad installation required — pure Python regex parsing.
Used as the single source of truth for all material and geometry data
fed into the EMerge simulation.

Author: Author
Version: 1.2.0
Last Updated: 2026-07-21
"""

import math
import re
import pathlib


# =========================================================================== #
# Pad position extraction
# =========================================================================== #

def read_pad_positions(pcb_path):
    """
    Return a dict mapping "REF:pad_number" → (x_mm, y_mm) for every pad
    in the PCB layout.

    Absolute position is computed as:
        abs = footprint_origin + rotate(local_pad_offset, footprint_angle)

    The .kicad_pcb file is the single source of truth — no pcbnew needed.

    Args:
        pcb_path: Path to .kicad_pcb file (str or pathlib.Path)

    Returns:
        dict: {"J1:1": (x_mm, y_mm), "U2:1": (x_mm, y_mm), ...}

    Example:
        pads = read_pad_positions("kicad/ad8317_powermeter.kicad_pcb")
        x, y = pads["J1:1"]   # SMA centre pin
        x, y = pads["U2:1"]   # AD8317 INHI
    """
    pads = {}
    content = pathlib.Path(pcb_path).read_text(encoding="utf-8")

    for fp_block in re.finditer(
            r'\(footprint\s.*?(?=\n\s*\(footprint|\Z)', content, re.DOTALL):
        # KiCad 9: (property "Reference" "J1" ...) — older versions used (reference "J1")
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_block.group()) or \
                re.search(r'\(reference\s+"([^"]+)"', fp_block.group())
        at_m  = re.search(
            r'^\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
            fp_block.group(), re.MULTILINE)
        if not ref_m or not at_m:
            continue

        ref  = ref_m.group(1)
        fp_x = float(at_m.group(1))
        fp_y = float(at_m.group(2))
        fp_a = math.radians(float(at_m.group(3)) if at_m.group(3) else 0.0)

        for pad_m in re.finditer(
                r'\(pad\s+"?(\w+)"?\s+\w+\s+\w+\s+\(at\s+([\d.\-]+)\s+([\d.\-]+)',
                fp_block.group()):
            lx = float(pad_m.group(2))
            ly = float(pad_m.group(3))
            # Rotate local pad offset by footprint angle
            ax = fp_x + lx * math.cos(fp_a) - ly * math.sin(fp_a)
            ay = fp_y + lx * math.sin(fp_a) + ly * math.cos(fp_a)
            pads[f"{ref}:{pad_m.group(1)}"] = (ax, ay)

    return pads


def list_pads_for_ref(pad_map, reference):
    """
    Return all pads for a given reference designator.

    Args:
        pad_map  : dict from read_pad_positions()
        reference: e.g. "J1", "U2"

    Returns:
        dict: {"1": (x, y), "2": (x, y), ...}
    """
    prefix = reference + ":"
    return {k[len(prefix):]: v
            for k, v in pad_map.items()
            if k.startswith(prefix)}


# =========================================================================== #
# Stackup extraction
# =========================================================================== #

def read_stackup(pcb_path):
    """
    Parse the (stackup ...) block from a .kicad_pcb file and return
    material/geometry parameters for use in EMerge.

    This function is the single source of truth for the board stackup —
    do not hardcode dielectric constants or layer thicknesses elsewhere.

    Args:
        pcb_path: Path to .kicad_pcb file (str or pathlib.Path)

    Returns:
        dict with keys:
            board_thickness_mm  (float) — total board thickness
            copper_thickness_mm (float) — copper foil thickness (1 oz = 0.035 mm)
            er                  (float) — dielectric constant of core
            tand                (float) — loss tangent of core
            copper_layers       (int)   — number of copper layers
            layers              (list)  — raw layer dicts (name, type, thick, er, tand)

    Example:
        st = read_stackup("kicad/ad8317_powermeter.kicad_pcb")
        print(st["er"])        # 4.5  (FR4 core)
        print(st["tand"])      # 0.02
    """
    content = pathlib.Path(pcb_path).read_text(encoding="utf-8")

    # Extract stackup block by counting parentheses (regex fails on nested parens)
    start = content.find("(stackup")
    if start == -1:
        print("WARNING: No (stackup ...) block found — "
              "using JLCPCB 2-layer FR4 1.6 mm defaults.")
        return {
            "board_thickness_mm":  1.6,
            "copper_thickness_mm": 0.035,
            "er":   4.5,
            "tand": 0.02,
            "copper_layers": 2,
            "layers": [],
        }

    depth, i, end = 0, start, start
    while i < len(content):
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
        i += 1
    block = content[start + len("(stackup"):end]

    layers = []

    for lm in re.finditer(
            r'\(layer\s+"([^"]+)"(.*?)(?=\(layer|\Z)', block, re.DOTALL):
        type_m  = re.search(r'\(type\s+"?([^"\)]+)"?\)', lm.group(2))
        thick_m = re.search(r'\(thickness\s+([\d.]+)\)', lm.group(2))
        er_m    = re.search(r'\(epsilon_r\s+([\d.]+)\)', lm.group(2))
        tand_m  = re.search(r'\(loss_tangent\s+([\d.]+)\)', lm.group(2))
        layers.append({
            "name":  lm.group(1),
            "type":  type_m.group(1).strip() if type_m else "",
            "thick": float(thick_m.group(1)) if thick_m else 0.0,
            "er":    float(er_m.group(1))    if er_m    else None,
            "tand":  float(tand_m.group(1))  if tand_m  else None,
        })

    core = next(
        (l for l in layers
         if l["type"] in ("core", "dielectric", "prepreg") and l["er"]),
        None)
    copper = [l for l in layers if l["type"] == "copper"]

    return {
        "board_thickness_mm":  sum(l["thick"] for l in layers),
        "copper_thickness_mm": max((l["thick"] for l in copper), default=0.035),
        "er":   core["er"]   if core else 4.5,
        "tand": core["tand"] if core else 0.02,
        "copper_layers": len(copper),
        "layers": layers,
    }


# =========================================================================== #
# Passive component extraction
# =========================================================================== #

def read_passive_components(pcb_path):
    """
    Return a list of passive SMD components whose reference designator starts
    with R, L, or C (e.g. R1, C12, L3).

    Reads pad centres and sizes directly from the .kicad_pcb — no pcbnew
    required.  Only components with exactly two numbered pads ("1" and "2")
    are returned; multi-pad ICs, connectors, and footprints without a valid
    Value property are silently skipped.

    Args:
        pcb_path : Path to .kicad_pcb (str or pathlib.Path)

    Returns:
        list of dict:
            ref       : "R1"
            value     : "100"        (raw KiCad Value string)
            layer     : "F.Cu" or "B.Cu"
            pad1_xy   : (x_mm, y_mm) absolute centre of pad "1"
            pad2_xy   : (x_mm, y_mm) absolute centre of pad "2"
            pad_size  : (w_mm, h_mm) size of pad "1" (pad-1 as proxy for body width)

    Example:
        comps = read_passive_components("kicad/board.kicad_pcb")
        for c in comps:
            print(c["ref"], c["value"], c["layer"])
    """
    components = []
    content = pathlib.Path(pcb_path).read_text(encoding="utf-8")

    for fp_block in re.finditer(
            r'\(footprint\s.*?(?=\n\s*\(footprint|\Z)', content, re.DOTALL):
        blk = fp_block.group()

        # Reference designator — must start with R, L, or C followed by digit
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', blk) or \
                re.search(r'\(reference\s+"([^"]+)"', blk)
        if not ref_m:
            continue
        ref = ref_m.group(1)
        if not re.match(r'^[RLCrlc]\d', ref):
            continue

        # Value property
        val_m = re.search(r'\(property\s+"Value"\s+"([^"]+)"', blk) or \
                re.search(r'\(value\s+"([^"]+)"', blk)
        value = val_m.group(1).strip() if val_m else ""
        if not value:
            continue

        # Layer — first occurrence of (layer "...") in the footprint block
        layer_m = re.search(r'\(footprint\s+[^\)]*\(layer\s+"([^"]+)"', blk) or \
                  re.search(r'^\s*\(layer\s+"([^"]+)"', blk, re.MULTILINE)
        layer = layer_m.group(1) if layer_m else "F.Cu"

        # Footprint origin and rotation
        at_m = re.search(
            r'^\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
            blk, re.MULTILINE)
        if not at_m:
            continue
        fp_x = float(at_m.group(1))
        fp_y = float(at_m.group(2))
        fp_a = math.radians(float(at_m.group(3)) if at_m.group(3) else 0.0)

        # Collect pads: absolute centre + size
        pad_data = {}
        for pad_m in re.finditer(
                r'\(pad\s+"?(\w+)"?\s+\w+\s+\w+\s+'
                r'\(at\s+([\d.\-]+)\s+([\d.\-]+)',
                blk):
            pnum = pad_m.group(1)
            lx   = float(pad_m.group(2))
            ly   = float(pad_m.group(3))
            ax   = fp_x + lx * math.cos(fp_a) - ly * math.sin(fp_a)
            ay   = fp_y + lx * math.sin(fp_a) + ly * math.cos(fp_a)
            # Size: search within ~300 chars following the pad keyword
            region   = blk[pad_m.start(): pad_m.start() + 350]
            size_m   = re.search(r'\(size\s+([\d.]+)\s+([\d.]+)\)', region)
            pw = float(size_m.group(1)) if size_m else 0.5
            ph = float(size_m.group(2)) if size_m else 0.5
            pad_data[pnum] = {"xy": (ax, ay), "size": (pw, ph)}

        if "1" not in pad_data or "2" not in pad_data:
            continue

        components.append({
            "ref":     ref,
            "value":   value,
            "layer":   layer,
            "pad1_xy": pad_data["1"]["xy"],
            "pad2_xy": pad_data["2"]["xy"],
            "pad_size": pad_data["1"]["size"],
        })

    return components


# =========================================================================== #
# Board outline extraction and point-in-polygon test
# =========================================================================== #

def read_board_outline(pcb_path):
    """
    Parse Edge.Cuts geometry and return the board outline as an ordered list
    of (x_mm, y_mm) vertices (closed polygon).

    Handles gr_line, gr_arc (approximated as straight chord), gr_rect, and
    gr_poly elements on the "Edge.Cuts" layer.  Returns [] when no Edge.Cuts
    geometry is found.

    Args:
        pcb_path: Path to .kicad_pcb

    Returns:
        list of (x_mm, y_mm) — polygon vertices in perimeter order, or []
    """
    content = pathlib.Path(pcb_path).read_text(encoding="utf-8")
    segments = []   # raw (x1, y1, x2, y2) line segments in mm

    # Regex that handles up to 2 levels of nesting inside ( ... )
    _blk = r'(?:[^()]*|\((?:[^()]*|\([^()]*\))*\))*'

    def _is_edge_cuts(blk):
        return '"Edge.Cuts"' in blk or "'Edge.Cuts'" in blk

    def _start_end(blk):
        s = re.search(r'\(start\s+([\d.\-]+)\s+([\d.\-]+)\)', blk)
        e = re.search(r'\(end\s+([\d.\-]+)\s+([\d.\-]+)\)', blk)
        if s and e:
            return (float(s.group(1)), float(s.group(2)),
                    float(e.group(1)), float(e.group(2)))
        return None

    # gr_line — direct start→end segment
    for m in re.finditer(r'\(gr_line\b' + _blk + r'\)', content, re.DOTALL):
        blk = m.group()
        if not _is_edge_cuts(blk):
            continue
        seg = _start_end(blk)
        if seg:
            segments.append(seg)

    # gr_arc — approximate as chord from start to end
    for m in re.finditer(r'\(gr_arc\b' + _blk + r'\)', content, re.DOTALL):
        blk = m.group()
        if not _is_edge_cuts(blk):
            continue
        seg = _start_end(blk)
        if seg:
            segments.append(seg)

    # gr_rect — expand to 4 segments
    for m in re.finditer(r'\(gr_rect\b' + _blk + r'\)', content, re.DOTALL):
        blk = m.group()
        if not _is_edge_cuts(blk):
            continue
        seg = _start_end(blk)
        if seg:
            x1, y1, x2, y2 = seg
            segments.extend([
                (x1, y1, x2, y1),
                (x2, y1, x2, y2),
                (x2, y2, x1, y2),
                (x1, y2, x1, y1),
            ])

    # gr_poly — extract vertices directly
    for m in re.finditer(r'\(gr_poly\b' + _blk + r'\)', content, re.DOTALL):
        blk = m.group()
        if not _is_edge_cuts(blk):
            continue
        pts_m = re.search(r'\(pts(.*?)\)', blk, re.DOTALL)
        if not pts_m:
            continue
        verts = re.findall(r'\(xy\s+([\d.\-]+)\s+([\d.\-]+)\)', pts_m.group(1))
        if len(verts) >= 2:
            coords = [(float(v[0]), float(v[1])) for v in verts]
            for i in range(len(coords)):
                x1, y1 = coords[i]
                x2, y2 = coords[(i + 1) % len(coords)]
                segments.append((x1, y1, x2, y2))

    if not segments:
        return []

    return _chain_outline(segments)


def _chain_outline(segments, tol=0.02):
    """
    Chain unordered line segments into closed polygons, then return the
    polygon with the largest area.

    Edge.Cuts may contain multiple closed loops — the main board outline
    plus smaller cutouts, mounting-hole circles, etc.  Taking the largest
    area guarantees we get the outer board boundary, not a hole.

    Returns list of (x, y) vertices, or [] if no closed polygon is found.
    """
    remaining = list(segments)
    chains = []

    while remaining:
        cx = [remaining[0][0], remaining[0][2]]
        cy = [remaining[0][1], remaining[0][3]]
        remaining.pop(0)

        for _ in range(len(remaining) + 1):
            if not remaining:
                break
            lx, ly = cx[-1], cy[-1]
            # Stop if the chain has closed back to its start point
            if len(cx) > 2 and math.hypot(lx - cx[0], ly - cy[0]) < tol:
                break
            found = False
            for i, (x1, y1, x2, y2) in enumerate(remaining):
                if math.hypot(x1 - lx, y1 - ly) < tol:
                    cx.append(x2); cy.append(y2)
                    remaining.pop(i); found = True; break
                elif math.hypot(x2 - lx, y2 - ly) < tol:
                    cx.append(x1); cy.append(y1)
                    remaining.pop(i); found = True; break
            if not found:
                break

        if len(cx) >= 3:
            chains.append(list(zip(cx, cy)))

    if not chains:
        return []

    # Return the polygon with the largest enclosed area (shoelace formula).
    # This selects the board outline over cutouts, mounting holes, etc.
    def _area(pts):
        n = len(pts)
        a = sum(pts[i][0] * pts[(i+1) % n][1] -
                pts[(i+1) % n][0] * pts[i][1]
                for i in range(n))
        return abs(a) * 0.5

    return max(chains, key=_area)


def point_in_board(x, y, outline_pts):
    """
    Ray-casting point-in-polygon test.

    Args:
        x, y        : point coordinates (same units as outline_pts)
        outline_pts : list of (x, y) from read_board_outline()

    Returns:
        True if (x, y) is inside the polygon, or if outline_pts is empty
        (no outline → no filtering).
    """
    if not outline_pts:
        return True
    n = len(outline_pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = outline_pts[i]
        xj, yj = outline_pts[j]
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def print_stackup_summary(pcb_path):
    """Print a human-readable stackup table for a given PCB file."""
    st = read_stackup(pcb_path)
    print(f"\nStackup: {pcb_path}")
    print(f"  Total thickness : {st['board_thickness_mm']:.3f} mm")
    print(f"  Copper thickness: {st['copper_thickness_mm']*1000:.0f} µm  "
          f"({st['copper_layers']} layers)")
    print(f"  Core: εr={st['er']}  tanδ={st['tand']}")
    print(f"\n  {'Layer':<20} {'Type':<12} {'Thick (mm)':<12} {'εr':<8} {'tanδ'}")
    print("  " + "-" * 60)
    for l in st["layers"]:
        er   = f"{l['er']}"   if l["er"]   is not None else "—"
        tand = f"{l['tand']}" if l["tand"] is not None else "—"
        thick = f"{l['thick']:.4f}" if l["thick"] else "—"
        print(f"  {l['name']:<20} {l['type']:<12} {thick:<12} {er:<8} {tand}")


# =========================================================================== #
# CLI — quick inspection tool
# =========================================================================== #

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect pad positions and stackup from a .kicad_pcb file")
    parser.add_argument("pcb", help="Path to .kicad_pcb")
    parser.add_argument("--pads", metavar="REF",
                        help="List all pads for a reference (e.g. J1, U2)")
    parser.add_argument("--stackup", action="store_true",
                        help="Print stackup summary")
    parser.add_argument("--all-pads", action="store_true",
                        help="Dump all pad positions")
    parser.add_argument("--passives", action="store_true",
                        help="List all R/L/C passive components")
    parser.add_argument("--outline", action="store_true",
                        help="Print board outline vertices and bounding box")
    args = parser.parse_args()

    if args.stackup:
        print_stackup_summary(args.pcb)

    if args.pads:
        pad_map = read_pad_positions(args.pcb)
        pads = list_pads_for_ref(pad_map, args.pads)
        print(f"\nPads for {args.pads}:")
        for num, (x, y) in sorted(pads.items(), key=lambda kv: kv[0]):
            print(f"  pad {num:>4} :  ({x:8.3f}, {y:8.3f}) mm")

    if args.all_pads:
        pad_map = read_pad_positions(args.pcb)
        print(f"\nAll pads ({len(pad_map)} total):")
        for key, (x, y) in sorted(pad_map.items()):
            print(f"  {key:<20}  ({x:8.3f}, {y:8.3f}) mm")

    if args.passives:
        comps = read_passive_components(args.pcb)
        print(f"\nPassive components ({len(comps)} found):")
        print(f"  {'Ref':<8} {'Value':<12} {'Layer':<8} "
              f"{'Pad1 (mm)':<22} {'Pad2 (mm)':<22} {'PadSize (mm)'}")
        print("  " + "-" * 80)
        for c in sorted(comps, key=lambda x: x["ref"]):
            x1, y1 = c["pad1_xy"]
            x2, y2 = c["pad2_xy"]
            pw, ph  = c["pad_size"]
            print(f"  {c['ref']:<8} {c['value']:<12} {c['layer']:<8} "
                  f"({x1:7.3f},{y1:7.3f})   ({x2:7.3f},{y2:7.3f})   "
                  f"{pw:.3f}×{ph:.3f}")

    if args.outline:
        pts = read_board_outline(args.pcb)
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            print(f"\nBoard outline: {len(pts)} vertices")
            print(f"  Bounding box: ({min(xs):.3f}, {min(ys):.3f}) – "
                  f"({max(xs):.3f}, {max(ys):.3f}) mm")
            for i, (x, y) in enumerate(pts):
                print(f"  [{i:3d}]  ({x:.3f}, {y:.3f})")
        else:
            print("No Edge.Cuts geometry found.")

    if not any([args.stackup, args.pads, args.all_pads, args.passives, args.outline]):
        parser.print_help()
