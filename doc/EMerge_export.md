# EMerge RF Simulation Pipeline

**KiCad + EMerge + Python — no FreeCAD required**

Simulates the RF input path (SMA connector → microstrip → AD8317) on the ad8317_powermeter PCB.
Outputs S-parameters (Touchstone `.s2p`/`.s1p`), Smith chart, input impedance plot, and a PDF report.

---

## Overview

```
KiCad PCB / Schematic
        │
        ├── kicad-cli pcb export gerbers   → fab/F_Cu.gbr, B_Cu.gbr, Edge_Cuts.gbr
        ├── kicad-cli pcb export drill     → fab/board.drl
        └── kicad-cli sch export netlist   → fab/board.cir  (R/L/C values)
                │
                ▼
        01_export_kicad.py
                │
                ▼
        02_run_emerge.py
        ┌───────────────────────────────────────┐
        │  FileBasedPCB.layer_from_file()        │  ← reads Gerbers directly
        │  FileBasedPCB.vias_from_file()         │  ← reads Excellon drill
        │  open_pml_region()                     │  ← auto air box + PML
        │  setLumpedElementToObject() per R/L/C  │  ← from KiCad SPICE netlist
        │  addLumpedPort() per simulation port   │  ← PORT1=SMA, PORT2=AD8317 INHI
        │  EMerge FEM sweep 100 MHz – 10 GHz     │
        │  export_touchstone() → .s2p / .s1p     │
        └───────────────────────────────────────┘
                │
                ▼
        03_spice_cosim.py
        ┌───────────────────────────────────────┐
        │  board.s2p as ngspice S element        │  ← PCB parasitics
        │  AD8317 SPICE model / R||C equivalent  │  ← active/IC devices
        │  ngspice AC sweep                      │
        │  pandoc + msedge → PDF report          │
        └───────────────────────────────────────┘
```

---

## Prerequisites

### Software

| Tool | Version | Notes |
|---|---|---|
| KiCad | 10.0 | `kicad-cli` must be on PATH |
| Python | 3.12 | EMerge is installed here |
| EMerge | 2.8.0 | `Python312/site-packages/emerge/` |
| basicemergesolverhelperpackage | — | same site-packages |
| emsutil | — | same site-packages |
| scikit-rf | any | `pip install scikit-rf` — for Smith chart |
| matplotlib | any | `pip install matplotlib` |
| pandoc | any | `winget install JohnMacFarlane.Pandoc` |
| Microsoft Edge | any | for headless PDF printing |

### KiCad PCB requirements

No special port footprints are needed. Ports are defined in the script by pointing at existing
component pads using `"REF:pad_number"` notation:

```text
"J1:1"   →  pad 1 of footprint J1  (SMA connector signal pin)
"U1:3"   →  pad 3 of footprint U1  (AD8317 INHI pin)
"R5:1"   →  pad 1 of footprint R5  (series resistor, one end)
```

The script reads the pad's absolute PCB coordinates directly from the `.kicad_pcb` file —
no dummy PORT1/PORT2 footprints, no manual coordinate entry.

**How to find the pad number:** In KiCad PCB editor, hover over any pad and the status bar shows
`Pad <number> of <REF>`. Pin numbers also appear in the schematic symbol.

### KiCad stackup — single source of truth

The script reads board thickness, copper weight, dielectric εr, and loss tangent
directly from the `.kicad_pcb` stackup block. No manual entry is needed.

Set the stackup in KiCad via **Board Setup → Board Stackup → Physical Stackup**:

| KiCad field | Used as | EMerge parameter |
| --- | --- | --- |
| Total board thickness | `board_thickness_mm` | `FileBasedPCB(thickness=...)` |
| Copper layer thickness | `copper_thickness_mm` | `trace_material` conductivity |
| Dielectric Epsilon R | `er` | `Material(er=...)` |
| Dielectric Loss Tangent | `loss_tangent` | `Material(tand=...)` |
| Number of copper layers | `copper_layers_count` | `FileBasedPCB(layers=...)` |

JLCPCB 2-layer standard defaults (used if stackup block is absent):
`1.6 mm` / `0.035 mm Cu` / `εr = 4.5` / `tanδ = 0.02`

---

## Script 01 — Export KiCad files

```python
# 01_export_kicad.py
import subprocess, pathlib

KICAD_PCB = pathlib.Path(r"C:\ad8317\ad8317.kicad_pcb")
KICAD_SCH = pathlib.Path(r"C:\ad8317\ad8317.kicad_sch")
FAB_DIR   = KICAD_PCB.parent / "fab"
FAB_DIR.mkdir(exist_ok=True)

KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"

def run(cmd):
    subprocess.run(cmd, check=True, shell=True)

# Copper layers + board outline
run(f'"{KICAD_CLI}" pcb export gerbers --output "{FAB_DIR}" "{KICAD_PCB}"')

# Drill / via positions
run(f'"{KICAD_CLI}" pcb export drill --output "{FAB_DIR}" "{KICAD_PCB}"')

# SPICE netlist — extracts all R/L/C component values
run(f'"{KICAD_CLI}" sch export netlist --format spice '
    f'--output "{FAB_DIR / "board.cir"}" "{KICAD_SCH}"')

print("Exported:")
for f in sorted(FAB_DIR.iterdir()):
    print(" ", f.name)
```

Run from the project root after any PCB or schematic change:

```powershell
python 01_export_kicad.py
```

---

## Script 02 — EMerge simulation

```python
# 02_run_emerge.py
import os, pathlib, glob, re
import numpy as np

import emerge as em
from emerge.beta.gerber import FileBasedPCB
from emerge._emerge.geo.open_region import open_pml_region
from emsutil import lib
from emsutil.material import Material
from emsutil.const import mm
from basicemergesolverhelperpackage import EMergeHelperFunctions
from basicemergesolverhelperpackage.EMergeConstants import (
    series_impedance, parallel_impedance)

# ── Paths ────────────────────────────────────────────────────────────────────
FAB_DIR   = pathlib.Path(r"C:\ad8317\fab")
SIM_DIR   = pathlib.Path(r"C:\ad8317\emerge_sim")
SIM_NAME  = "ad8317_rf_input"
SIM_DIR.mkdir(exist_ok=True)
os.chdir(SIM_DIR)

# ── Read stackup from .kicad_pcb  (single source of truth) ──────────────────
def read_kicad_stackup(kicad_pcb_path):
    """
    Parse the (stackup ...) block from a .kicad_pcb file.

    Returns a dict with:
      'board_thickness_mm'  : total PCB thickness (mm)
      'copper_thickness_mm' : thickest copper layer thickness (mm)
                              KiCad stores copper weight as thickness in mm,
                              e.g. 0.035 = 1 oz, 0.070 = 2 oz
      'layers'              : list of dicts, one per layer in stackup order:
           {'name': str, 'type': 'copper'|'dielectric'|'soldermask'|...,
            'thickness_mm': float,
            'er': float or None,       # only for dielectric layers
            'loss_tangent': float or None}
      'copper_layers_count' : number of copper layers
    """
    with open(kicad_pcb_path) as f:
        content = f.read()

    # Extract the stackup block
    m = re.search(r'\(stackup(.*?)\)\s*\(', content, re.DOTALL)
    if not m:
        print("WARNING: no stackup block found in .kicad_pcb — using defaults")
        return {
            'board_thickness_mm': 1.6,
            'copper_thickness_mm': 0.035,
            'layers': [],
            'copper_layers_count': 2,
        }
    block = m.group(1)

    layers = []
    copper_layers = 0
    for layer_m in re.finditer(r'\(layer\s+"([^"]+)"(.*?)(?=\(layer|\Z)', block, re.DOTALL):
        name = layer_m.group(1)
        body = layer_m.group(2)

        type_m  = re.search(r'\(type\s+"?([^"\)]+)"?\)', body)
        thick_m = re.search(r'\(thickness\s+([\d.]+)\)', body)
        er_m    = re.search(r'\(epsilon_r\s+([\d.]+)\)', body)
        tand_m  = re.search(r'\(loss_tangent\s+([\d.]+)\)', body)

        ltype = type_m.group(1).strip() if type_m else 'unknown'
        thick = float(thick_m.group(1)) if thick_m else 0.0
        er    = float(er_m.group(1))    if er_m    else None
        tand  = float(tand_m.group(1))  if tand_m  else None

        layers.append({'name': name, 'type': ltype,
                       'thickness_mm': thick, 'er': er, 'loss_tangent': tand})
        if ltype == 'copper':
            copper_layers += 1

    # Board thickness: look for top-level (thickness ...) outside stackup layers
    board_thick_m = re.search(r'^\s*\(thickness\s+([\d.]+)\)', block, re.MULTILINE)
    if not board_thick_m:
        board_thick_m = re.search(r'\(board_thickness\s+([\d.]+)\)', content)
    board_thick = float(board_thick_m.group(1)) if board_thick_m \
                  else sum(l['thickness_mm'] for l in layers)

    copper_thick = max(
        (l['thickness_mm'] for l in layers if l['type'] == 'copper'),
        default=0.035)

    print(f"Stackup read from PCB: {copper_layers} copper layers, "
          f"{board_thick:.3f} mm total")
    for l in layers:
        if l['type'] == 'copper':
            print(f"  {l['name']:<20} copper   {l['thickness_mm']*1000:.0f} µm")
        elif l['type'] in ('dielectric', 'core', 'prepreg'):
            print(f"  {l['name']:<20} dielec   {l['thickness_mm']:.4f} mm  "
                  f"εr={l['er']}  tanδ={l['loss_tangent']}")
    return {
        'board_thickness_mm': board_thick,
        'copper_thickness_mm': copper_thick,
        'layers': layers,
        'copper_layers_count': copper_layers,
    }

KICAD_PCB_PATH = FAB_DIR.parent / "ad8317.kicad_pcb"
stackup = read_kicad_stackup(KICAD_PCB_PATH)

BOARD_THICKNESS_MM  = stackup['board_thickness_mm']
COPPER_THICKNESS_MM = stackup['copper_thickness_mm']
COPPER_COND         = 5.8e7   # S/m — physical constant, not a KiCad parameter

# Build dielectric material from stackup (use core layer εr and tanδ if present)
_core = next((l for l in stackup['layers']
              if l['type'] in ('dielectric', 'core', 'prepreg') and l['er']), None)
_er   = _core['er']           if _core and _core['er']           else 4.5
_tand = _core['loss_tangent'] if _core and _core['loss_tangent'] else 0.02

# ── Simulation parameters ────────────────────────────────────────────────────
FREQ_MIN      = 100e6    # 100 MHz
FREQ_MAX      = 10e9     # 10 GHz
NPOINTS       = 51
Z0_REF        = 50.0     # Ω reference impedance
AIR_MARGIN_MM = 5.0      # air box margin around PCB
PML_THICK_MM  = 2.0      # PML absorbing layer thickness

# ── Materials — built from KiCad stackup ────────────────────────────────────
fr4    = Material(name="FR4",    er=_er, ur=1.0, tand=_tand, cond=0)
copper = Material(name="copper", cond=COPPER_COND, _metal=True)
print(f"Dielectric: εr={_er}, tanδ={_tand}")

# ── PCB from Gerber files ────────────────────────────────────────────────────
def find_gerber(layer_suffix):
    matches = list(FAB_DIR.glob(f"*{layer_suffix}*"))
    if not matches:
        raise FileNotFoundError(f"No Gerber found for layer '{layer_suffix}' in {FAB_DIR}")
    return str(matches[0])

pcb = FileBasedPCB(
    thickness=BOARD_THICKNESS_MM,           # from KiCad stackup
    unit=0.001,                             # 1 unit = 1 mm
    material=fr4,                           # εr / tanδ from KiCad stackup
    layers=stackup['copper_layers_count'],  # 2, 4, 6 ... from KiCad stackup
    trace_material=copper,
)

# layer index: -1 = F.Cu (top), 0 = B.Cu (bottom for 2-layer), 1,2,... = inner
# Gerber file names match the KiCad layer names in the stackup,
# e.g.  "F.Cu" → fab/ad8317-F_Cu.gbr
pcb.layer_from_file(layer=-1, filename=find_gerber("F_Cu"),  res_mm=0.01)
pcb.layer_from_file(layer=0,  filename=find_gerber("B_Cu"),  res_mm=0.01)

# For 4-layer boards add inner layers using names from stackup['layers']:
# pcb.layer_from_file(layer=1, filename=find_gerber("In1_Cu"), res_mm=0.01)
# pcb.layer_from_file(layer=2, filename=find_gerber("In2_Cu"), res_mm=0.01)

pcb.vias_from_file(str(list(FAB_DIR.glob("*.drl"))[0]))
pcb.generate_pcb(split_z=True, merge=True)

# ── Air box with PML absorbing boundary ─────────────────────────────────────
# open_pml_region auto-detects the geometry bounding box; PML on all 6 faces
open_pml_region(
    xmargin=AIR_MARGIN_MM,
    ymargin=AIR_MARGIN_MM,
    zmargin=AIR_MARGIN_MM,
    material=lib.AIR,
    thickness=PML_THICK_MM,
    Nlayers=1,
    N_mesh_layers=5,
    exponent=1.5,
    deltamax=8.0,
    sides='TBLRFA',        # Top, Bottom, Left, Right, Front, bAck
)

# ── Parse R/L/C values from KiCad SPICE netlist ──────────────────────────────
def parse_spice_components(cir_path):
    """
    Returns {ref: {'type': 'R'|'L'|'C', 'value': float}} for all passives.
    Parses KiCad spice netlist format (kicad-cli sch export netlist --format spice).
    """
    mul = {'':1,'k':1e3,'K':1e3,'M':1e6,'m':1e-3,'u':1e-6,'n':1e-9,'p':1e-12,'f':1e-15}
    def to_si(s):
        m = re.match(r'([0-9.]+)\s*([a-zA-Zµ]*)', s.strip())
        if not m: return None
        num, sfx = float(m.group(1)), m.group(2).rstrip('FHΩohm')
        return num * mul.get(sfx, 1)

    result = {}
    with open(cir_path) as f:
        for line in f:
            line = line.strip()
            if not line or line[0] in ('*', '.'): continue
            parts = line.split()
            ref = parts[0].upper()
            if ref[0] in ('R', 'C', 'L'):
                val = to_si(parts[-1])
                if val: result[ref] = {'type': ref[0], 'value': val}
    return result

components = parse_spice_components(FAB_DIR / "board.cir")
print(f"Found {len(components)} passives in netlist: "
      f"{sum(1 for c in components.values() if c['type']=='R')} R, "
      f"{sum(1 for c in components.values() if c['type']=='C')} C, "
      f"{sum(1 for c in components.values() if c['type']=='L')} L")

# ── Read absolute pad positions from .kicad_pcb ───────────────────────────────
def get_pad_positions(kicad_pcb_path):
    """
    Returns {"REF:pad": (x_abs_mm, y_abs_mm)} for every pad in the PCB.

    KiCad stores pad positions as local offsets relative to the footprint origin.
    Absolute position = footprint_origin + rotate(pad_local, footprint_angle).

    Usage:
        pads = get_pad_positions("board.kicad_pcb")
        x, y = pads["J1:1"]    # SMA pad 1
        x, y = pads["U1:3"]    # AD8317 INHI pin (pin 3)
        x, y = pads["R5:1"]    # resistor pad 1
    """
    import math
    pads = {}
    with open(kicad_pcb_path) as f:
        content = f.read()

    for block in re.finditer(r'\(footprint\s.*?(?=\n\s*\(footprint|\Z)', content, re.DOTALL):
        ref_m = re.search(r'\(reference\s+"([^"]+)"', block.group())
        fp_at = re.search(r'^\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
                          block.group(), re.MULTILINE)
        if not ref_m or not fp_at:
            continue
        ref   = ref_m.group(1)
        fp_x  = float(fp_at.group(1))
        fp_y  = float(fp_at.group(2))
        fp_a  = math.radians(float(fp_at.group(3)) if fp_at.group(3) else 0.0)

        for pad_m in re.finditer(
                r'\(pad\s+"?(\w+)"?\s+\w+\s+\w+\s+\(at\s+([\d.\-]+)\s+([\d.\-]+)',
                block.group()):
            pad_num  = pad_m.group(1)
            lx, ly   = float(pad_m.group(2)), float(pad_m.group(3))
            # rotate local pad offset by footprint angle, then add footprint origin
            ax = fp_x + lx * math.cos(fp_a) - ly * math.sin(fp_a)
            ay = fp_y + lx * math.sin(fp_a) + ly * math.cos(fp_a)
            pads[f"{ref}:{pad_num}"] = (ax, ay)

    return pads

pads = get_pad_positions(FAB_DIR.parent / "ad8317.kicad_pcb")

# Print all detected pads for reference during setup
print("Detected pads (REF:pad  →  x_mm, y_mm):")
for key, (x, y) in sorted(pads.items()):
    print(f"  {key:<12}  {x:7.3f}  {y:7.3f}")

# ── Build simulation ─────────────────────────────────────────────────────────
sim    = em.Simulation(SIM_NAME, save_file=True)
helper = EMergeHelperFunctions(sim)

try:
    sim.mw.solveroutine.set_solver(em.EMSolver.CUDSS)   # GPU (NVIDIA)
except Exception:
    sim.mw.solveroutine.set_solver(em.EMSolver.MUMPS)   # CPU fallback

sim.mw.set_frequency_range(FREQ_MIN, FREQ_MAX, NPOINTS)

# ── Add lumped elements for R/L/C components ─────────────────────────────────
# Component geometry: span between pad 1 and pad 2 defines width and centre.
# The script reads actual pad coordinates — no hardcoded component sizes needed.

for ref, comp in components.items():
    key1, key2 = f"{ref}:1", f"{ref}:2"
    if key1 not in pads or key2 not in pads:
        print(f"  skip {ref}: pads not found in PCB")
        continue

    x1, y1 = pads[key1]
    x2, y2 = pads[key2]
    cx, cy  = (x1 + x2) / 2, (y1 + y2) / 2          # component centre
    w_mm    = abs(x2 - x1) or 1.5                     # pad-to-pad distance in X
    h_mm    = abs(y2 - y1) or 0.8                     # pad-to-pad distance in Y
    if w_mm < 0.1: w_mm = 1.5                         # guard for vertical placement
    if h_mm < 0.1: h_mm = 0.8

    em.geo.Plate(
        origin=[(cx - w_mm/2)*mm, (cy - h_mm/2)*mm, pcb.z(-1)],
        u=[w_mm*mm, 0, 0],
        v=[0, h_mm*mm, 0],
        name=ref,
    )

    if   comp['type'] == 'R': Z = series_impedance(R=comp['value'])
    elif comp['type'] == 'C': Z = series_impedance(C=comp['value'])
    elif comp['type'] == 'L': Z = series_impedance(L=comp['value'])
    else: continue

    helper.setLumpedElementToObject(
        name=ref, impedance_function=Z,
        width=w_mm*mm, height=h_mm*mm)

# ── Simulation ports ─────────────────────────────────────────────────────────
#
# Define each port as "REF:pad" — the script looks up the absolute pad
# coordinates from the .kicad_pcb file automatically.  No dummy footprints.
#
# "pad" key format: "J1:1"  →  pad number 1 of footprint J1
#
# IC/transistor modelling note:
#   EM simulators only support linear, frequency-domain impedances.
#   Model IC inputs/outputs as their linear equivalent from the datasheet.
#   AD8317 INHI: Rin = 200 Ω, Cin = 2 pF  (AD8317 datasheet Table 1)
#   For full nonlinear behavior use ngspice co-simulation (script 03).

PORT_DEFS = {
    # name   pad (REF:num)   R (Ω)    C (F)     active   direction
    "PORT1": {"pad": "J1:1",  "R": 50.0,  "C": None,   "active": True,  "dir": em.ZAX},
    "PORT2": {"pad": "U1:3",  "R": 200.0, "C": 2e-12,  "active": False, "dir": em.ZAX},
    # Add more ports as needed, e.g.:
    # "PORT3": {"pad": "U1:5", "R": 50.0, "C": None, "active": False, "dir": em.ZAX},
}

# Port plate size — should match the pad size in KiCad (adjust per footprint)
PORT_W_MM = 1.0
PORT_H_MM = 1.0

for port_name, pdef in PORT_DEFS.items():
    pad_key = pdef["pad"]
    if pad_key not in pads:
        print(f"WARNING: pad '{pad_key}' not found in PCB — skipping {port_name}")
        print(f"  Available pads for this ref: "
              f"{[k for k in pads if k.startswith(pad_key.split(':')[0])]}")
        continue

    x, y  = pads[pad_key]
    z_bot = pcb.z(0)
    print(f"  {port_name}  ←  {pad_key}  at ({x:.3f}, {y:.3f}) mm")

    port_geo = em.geo.Plate(
        origin=[(x - PORT_W_MM/2)*mm, (y - PORT_H_MM/2)*mm, z_bot],
        u=[PORT_W_MM*mm, 0, 0],
        v=[0, PORT_H_MM*mm, 0],
        name=port_name,
    )

    Z = parallel_impedance(R=pdef["R"], C=pdef["C"]) if pdef["C"] \
        else series_impedance(R=pdef["R"])

    helper.addLumpedPort(
        name=port_name,
        portStart=[(x - PORT_W_MM/2)*mm, (y - PORT_H_MM/2)*mm, z_bot],
        width=PORT_W_MM*mm, height=PORT_H_MM*mm,
        R=pdef["R"],
        direction=pdef["dir"],
        power=1.0 if pdef["active"] else 0.0,
        geometryObject=port_geo,
    )

# ── Mesh ─────────────────────────────────────────────────────────────────────
helper.setObjSize(name="top_copper",     size=0.1*mm)
helper.setObjSize(name="bot_copper",     size=0.1*mm)
helper.setObjSize(name="pcb_volume",     size=0.3*mm)
helper.setObjDomainSize(name="pcb_volume", size=1.0*mm)

# ── Solve ────────────────────────────────────────────────────────────────────
sim.commit_geometry()
sim.generate_mesh()
sim.export(os.path.join("mesh", f"{SIM_NAME}.msh"))

for port_name in PORT_DEFS:
    helper.setPortAsLumpedPort(port_name)

sim.settings.check_ram = False
simulationResult = sim.mw.run_sweep()
sim.save()

# ── Export Touchstone ────────────────────────────────────────────────────────
res = sim.data.mw

res.scalar.grid.export_touchstone(
    f"{SIM_NAME}.s2p", Z0ref=Z0_REF, format="RI", funit="GHz")

dense_f = res.scalar.grid.dense_f(501)
res.scalar.grid.export_touchstone(
    f"{SIM_NAME}_dense.s2p", Z0ref=Z0_REF, format="RI", funit="GHz",
    dense_freq=dense_f)

res.scalar.grid.export_touchstone(
    f"{SIM_NAME}_port1.s1p", Z0ref=Z0_REF, format="RI", funit="GHz")

helper.exportCSV_SParam(f"{SIM_NAME}_s11.csv", 1, 1)
helper.exportCSV_SParam(f"{SIM_NAME}_s21.csv", 1, 2)

print(f"\nResults written to {SIM_DIR}/")
print(f"  {SIM_NAME}.s2p          2-port Touchstone, 50 Ω, RI format")
print(f"  {SIM_NAME}_dense.s2p    501-point interpolated")
print(f"  {SIM_NAME}_port1.s1p    S11 single-port")
print(f"  {SIM_NAME}_s11.csv      S11 dB + phase")
print(f"  {SIM_NAME}_s21.csv      S21 dB + phase")
```

---

## Script 03 — SPICE co-simulation

Active devices (IC, transistor, diode) cannot be modelled inside EMerge.  
The PCB structure from EMerge becomes an `S` element in ngspice. Active devices attach as normal SPICE models.

### IC / active device modelling options

| Device | In EMerge | In ngspice |
|---|---|---|
| Passive R/L/C | `setLumpedElementToObject()` | extracted automatically from netlist |
| IC RF input (AD8317) | `parallel_impedance(R=200, C=2e-12)` | or full `.lib` subcircuit |
| Transistor BJT/MOSFET | not possible | `.model` / `.subckt` |
| Diode | not possible | `.model D` |
| IC with gain | not possible | `.subckt` behavioural model |

```python
# 03_spice_cosim.py
import subprocess, pathlib, csv
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SIM_DIR  = pathlib.Path(r"C:\ad8317\emerge_sim")
SIM_NAME = "ad8317_rf_input"
NGSPICE  = r"C:\Program Files\KiCad\10.0\bin\ngspice.exe"

# ── Generate ngspice netlist ──────────────────────────────────────────────────
netlist = f"""* ad8317_powermeter RF input co-simulation
* PCB structure (SMA → microstrip → AD8317 pad) from EMerge Touchstone
* AD8317 INHI modelled as 200 Ω || 2 pF  (datasheet Table 1)
.title AD8317 RF Input

Vsrc  rf_in  0  AC 1

* Two-port PCB Touchstone model from EMerge
S_pcb  rf_in 0  ad8317_in 0  params="{SIM_DIR / SIM_NAME}.s2p"

* AD8317 input equivalent (linear EM model — use .lib for full detector)
R_in  ad8317_in  0  200
C_in  ad8317_in  0  2p

* Uncomment to use Analog Devices SPICE model instead:
* .lib "AD8317.lib"
* Xad8317  ad8317_in  0  vout  AD8317

.ac dec 100 100meg 10g

.end
"""

cir_path = SIM_DIR / f"{SIM_NAME}_cosim.cir"
cir_path.write_text(netlist)

result = subprocess.run(
    [NGSPICE, "-b", "-o", str(SIM_DIR / f"{SIM_NAME}_ngspice.log"), str(cir_path)],
    capture_output=True, text=True)
if result.returncode != 0:
    print(result.stderr[-2000:])
    raise RuntimeError("ngspice failed")

# ── Plot results ──────────────────────────────────────────────────────────────
def load_csv(path):
    freqs, vals = [], []
    with open(path) as f:
        for row in csv.DictReader(f):
            freqs.append(float(row["Frequency"]) / 1e9)
            vals.append(float(row["S_dB"]))
    return np.array(freqs), np.array(vals)

f11, s11 = load_csv(SIM_DIR / f"{SIM_NAME}_s11.csv")
f21, s21 = load_csv(SIM_DIR / f"{SIM_NAME}_s21.csv")

fig, axes = plt.subplots(2, 2, figsize=(13, 9))
fig.suptitle(f"AD8317 Power Meter — RF Input Path\n"
             f"EMerge FEM ({f11[0]:.0f}–{f11[-1]:.0f} GHz, {len(f11)} pts) "
             f"+ ngspice co-simulation", fontsize=12)

axes[0,0].plot(f11, s11, 'b')
axes[0,0].axhline(-10, c='r', ls='--', lw=0.8, label="−10 dB")
axes[0,0].set(xlabel="f (GHz)", ylabel="|S11| (dB)", title="Return Loss S11")
axes[0,0].grid(True, alpha=0.3); axes[0,0].legend()

axes[0,1].plot(f21, s21, 'g')
axes[0,1].set(xlabel="f (GHz)", ylabel="|S21| (dB)", title="Insertion Loss S21")
axes[0,1].grid(True, alpha=0.3)

try:
    import skrf as rf
    ntwk = rf.Network(str(SIM_DIR / f"{SIM_NAME}_port1.s1p"))
    ntwk.plot_s_smith(ax=axes[1,0], show_legend=False)
    axes[1,0].set_title("Smith Chart S11")

    ntwk2 = rf.Network(str(SIM_DIR / f"{SIM_NAME}_dense.s2p"))
    axes[1,1].plot(ntwk2.f/1e9, ntwk2.z[:,0,0].real, 'b-',  label="Re(Zin)")
    axes[1,1].plot(ntwk2.f/1e9, ntwk2.z[:,0,0].imag, 'r--', label="Im(Zin)")
    axes[1,1].axhline(50, c='k', ls=':', lw=0.8, label="50 Ω")
    axes[1,1].set(xlabel="f (GHz)", ylabel="Impedance (Ω)", title="Input Impedance")
    axes[1,1].legend(); axes[1,1].grid(True, alpha=0.3)
except ImportError:
    for ax in axes[1]:
        ax.text(0.5, 0.5, "pip install scikit-rf", ha='center', va='center',
                transform=ax.transAxes)

plt.tight_layout()
png_path = SIM_DIR / f"{SIM_NAME}_report.png"
plt.savefig(png_path, dpi=150, bbox_inches='tight'); plt.close()

# ── Generate PDF report ───────────────────────────────────────────────────────
md = f"""# RF Input Simulation — ad8317_powermeter

## Simulation method
- EM solver: EMerge FEM, {f11[0]:.0f}–{f11[-1]:.0f} GHz, {len(f11)} frequency points
- Co-simulation: ngspice AC analysis, PCB as Touchstone S-element
- AD8317 INHI modelled as 200 Ω ∥ 2 pF (datasheet linear equivalent)

## S-parameter summary

| | Min (dB) | f (GHz) | Max (dB) | f (GHz) |
|---|---|---|---|---|
| S11 | {s11.min():.1f} | {f11[s11.argmin()]:.2f} | {s11.max():.1f} | {f11[s11.argmax()]:.2f} |
| S21 | {s21.min():.1f} | {f21[s21.argmin()]:.2f} | {s21.max():.1f} | {f21[s21.argmax()]:.2f} |

![Results]({png_path})

## Output files

| File | Contents |
|---|---|
| `{SIM_NAME}.s2p` | 2-port Touchstone, 50 Ω reference |
| `{SIM_NAME}_dense.s2p` | 501-point interpolated |
| `{SIM_NAME}_port1.s1p` | S11 single-port |
| `{SIM_NAME}_s11.csv` | S11 magnitude (dB) + phase |
| `{SIM_NAME}_s21.csv` | S21 magnitude (dB) + phase |
| `{SIM_NAME}_cosim.cir` | ngspice netlist with Touchstone S element |
"""

md_path   = SIM_DIR / f"{SIM_NAME}_report.md"
html_path = SIM_DIR / f"{SIM_NAME}_report.html"
pdf_path  = SIM_DIR / f"{SIM_NAME}_report.pdf"
md_path.write_text(md)

import subprocess as sp
sp.run(["pandoc", str(md_path), "-o", str(html_path), "--self-contained"], check=True)
sp.run(["msedge", "--headless", "--disable-gpu",
        f"--print-to-pdf={pdf_path}", str(html_path)], check=True)

print(f"Report: {pdf_path}")
```

---

## Master run script

```powershell
# master_run.ps1 — run from project root
# Create junction first to avoid OneDrive spaces in path
cmd /c "mklink /J C:\ad8317 `"$PWD`""
try {
    Set-Location C:\ad8317
    python 01_export_kicad.py
    python 02_run_emerge.py
    python 03_spice_cosim.py
} finally {
    Set-Location $PSScriptRoot
    Remove-Item C:\ad8317 -Force -ErrorAction SilentlyContinue
}
```

---

## EMerge API reference

### FileBasedPCB (Gerber reader)

```python
from emerge.beta.gerber import FileBasedPCB

pcb = FileBasedPCB(thickness, unit, material, layers, trace_material)
pcb.layer_from_file(layer, filename, res_mm, n_circ_segments)
# layer: -1 = F.Cu (top), 0 = B.Cu (bottom), 1,2,... = inner layers
pcb.vias_from_file(filename, z1=None, z2=None)
pcb.generate_pcb(split_z=True, merge=True)
pcb.z(layer)     # returns z-coordinate of layer in simulation units
```

### Air box with PML

```python
from emerge._emerge.geo.open_region import open_pml_region

open_pml_region(
    xmargin, ymargin, zmargin,    # air gap around PCB (simulation units)
    material=lib.AIR,
    thickness=2.0,                # PML layer thickness
    Nlayers=1,                    # number of PML shells
    N_mesh_layers=5,              # mesh refinement inside PML
    exponent=1.5,                 # PML conductivity profile
    deltamax=8.0,                 # max element size in PML
    sides='TBLRFA',               # T=Top B=Bottom L=Left R=Right F=Front A=bAck
)
```

### Lumped element impedances

```python
from basicemergesolverhelperpackage.EMergeConstants import (
    series_impedance, parallel_impedance)

series_impedance(R=None, L=None, C=None)    # R + jωL + 1/(jωC)
parallel_impedance(R=None, L=None, C=None)  # R ∥ jωL ∥ 1/(jωC)

# Examples:
series_impedance(R=50)            # 50 Ω resistor
series_impedance(C=100e-9)        # 100 nF capacitor
series_impedance(L=10e-9)         # 10 nH inductor
parallel_impedance(R=200, C=2e-12)  # AD8317 INHI: 200 Ω || 2 pF
```

### Touchstone export

```python
res = sim.data.mw
res.scalar.grid.export_touchstone(
    "output.s2p",
    Z0ref=50,       # reference impedance (Ω)
    format="RI",    # "RI" = real/imag, "MA" = magnitude/angle, "DB" = dB/angle
    funit="GHz",    # "Hz", "MHz", "GHz"
)
dense_f = res.scalar.grid.dense_f(501)
res.scalar.grid.export_touchstone("output_dense.s2p", ..., dense_freq=dense_f)
```

### Materials

```python
from emsutil import lib
from emsutil.material import Material

fr4    = Material(name="FR4",    er=4.5, ur=1.0, tand=0.02, cond=0)
copper = Material(name="copper", cond=5.8e7, _metal=True)
lib.AIR      # predefined air
lib.PEC      # perfect electric conductor
lib.COPPER   # predefined copper
```

---

## Modelling active devices

EMerge (and all EM simulators) only supports **linear, frequency-domain impedances**.
Nonlinear devices must be handled in SPICE co-simulation.

| Device | EMerge model | ngspice model |
|---|---|---|
| Resistor | `series_impedance(R=val)` | `.r` element (auto from KiCad) |
| Capacitor | `series_impedance(C=val)` | `.c` element (auto from KiCad) |
| Inductor / ferrite bead | `series_impedance(L=val)` | `.l` element |
| IC RF input | `parallel_impedance(R=Rin, C=Cin)` | `.lib` subcircuit |
| IC RF output | `series_impedance(R=Rout)` | `.lib` subcircuit |
| Transistor BJT/MOSFET | not supported | `.model` / `.subckt` |
| Diode | not supported | `.model D` |
| AD8317 detector | `parallel_impedance(R=200, C=2e-12)` | `AD8317.lib` (Analog Devices) |

For the full detector power-to-voltage characteristic, use the ngspice behavioural model:

```spice
* AD8317 log-law approximation (simplified)
* Vout = -25 * log10(Pin_mW) + Vslope_intercept
Edet  vout  0  VALUE={-25e-3 * log10(V(ad8317_in)^2 / (2*50) * 1000) + 1.8}
```

---

## Troubleshooting

| Issue | Cause | Fix |
|---|---|---|
| `FileNotFoundError` on Gerber | Layer name mismatch | Check `kicad-cli` output — KiCad 10 names layers `ad8317-F_Cu.gbr` |
| `WARNING: pad 'J1:1' not found` | Wrong ref or pad number | Script prints available pads for that ref; check KiCad status bar for exact pad number |
| Port at wrong position | Pad number is for wrong pin | In KiCad PCB editor hover the pad — status bar shows `Pad <N> of <REF>` |
| Lumped element skipped | Footprint has only 1 pad (e.g. via, mounting hole) | Normal — script skips any ref without both pad 1 and pad 2 |
| EMerge solver fails (CUDSS) | No NVIDIA GPU | Script falls back to MUMPS automatically |
| ngspice `S element` error | KiCad's ngspice version | Use `ngspice.exe` from KiCad 10 installation |
| OneDrive spaces in path | GNU Make / arm-none-eabi-ld | Use `cmd /c mklink /J C:\ad8317 "..."` junction |
| Mesh too coarse | `res_mm` too large | Decrease `res_mm` in `layer_from_file()` — halving doubles mesh size |
