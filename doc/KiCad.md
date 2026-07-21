# KiCad File Formats — Read, Write, Modify

Practical reference for the ad8317_powermeter project (KiCad 9.0.x).  
Covers: file format anatomy, Python scripting, `kicad-cli`, and direct S-expression editing.

---

## File types

| File | Format | Purpose |
| :--- | :--- | :--- |
| `*.kicad_pro` | JSON | Project settings, net classes, BOM config |
| `*.kicad_sch` | S-expression | Schematic (symbols, wires, labels, sheets) |
| `*.kicad_pcb` | S-expression | PCB layout (footprints, tracks, zones, stackup) |
| `*.kicad_sym` | S-expression | Symbol library |
| `*.kicad_mod` | S-expression | Footprint library entry |
| `sym-lib-table` | S-expression | Per-project symbol library paths |
| `fp-lib-table` | S-expression | Per-project footprint library paths |
| `*.kicad_dru` | Text | Custom DRC rules |

All S-expression files use the same syntax:

```text
(token value
  (child token value)
  (child token value)
)
```

Strings are double-quoted. Numbers are unquoted. KiCad 9 uses **tab indentation**.

---

## S-expression rules (KiCad 9)

### Always required — schematic `*.kicad_sch`

```text
(kicad_sch
  (version 20250114)          ← KiCad 9 schematic version
  (generator "eeschema")
  (generator_version "9.0")
  (uuid "xxxxxxxx-...")       ← unique per file, generate with uuidgen
  (paper "A4")
  (title_block ...)
  (lib_symbols)               ← empty if no inline symbols
  ...symbols, wires, labels...
  (sheet_instances
    (path "/"
      (page "1")
    )
  )
  (embedded_fonts no)
)
```

### Always required — PCB `*.kicad_pcb`

```text
(kicad_pcb
  (version 20241229)          ← KiCad 9 PCB version
  (generator "pcbnew")
  (generator_version "9.0")
  (general
    (thickness 1.6)           ← total board thickness mm
    (legacy_teardrops no)
  )
  (paper "A4")
  (title_block ...)
  (layers ...)                ← layer numbering (see below)
  (setup
    (stackup ...)             ← dielectric, copper, mask layers
    (pad_to_mask_clearance 0)
    (pcbplotparams ...)
  )
  (net 0 "")
  (net 1 "GND")
  ...footprints, tracks, zones...
  (embedded_fonts no)
)
```

### `(at X Y ANGLE)` — always three values in KiCad 9

```text
(at 50.0 30.0 0)     ← X mm, Y mm, rotation degrees
(at 100.0 45.5 90)   ← rotated 90°
```

**Omitting the angle causes "need a number for text angle" error.**

### `effects` — always expanded (multi-line) in KiCad 9

```text
;; CORRECT — KiCad 9
(effects
  (font
    (size 1.27 1.27)
  )
  (justify left)
)

;; WRONG — causes parse error
(effects (font (size 1.27 1.27)) (justify left))
```

---

## Layer numbering — KiCad 9 PCB

KiCad 9 changed layer numbers from the KiCad 8 scheme.

| Layer | Number | Type |
| :--- | :--- | :--- |
| `F.Cu` | 0 | copper signal |
| `B.Cu` | 2 | copper signal |
| `F.Mask` | 1 | user |
| `B.Mask` | 3 | user |
| `F.SilkS` | 5 | user "F.Silkscreen" |
| `B.SilkS` | 7 | user "B.Silkscreen" |
| `F.Adhes` | 9 | user "F.Adhesive" |
| `B.Adhes` | 11 | user "B.Adhesive" |
| `F.Paste` | 13 | user |
| `B.Paste` | 15 | user |
| `Dwgs.User` | 17 | user "User.Drawings" |
| `Cmts.User` | 19 | user "User.Comments" |
| `Eco1.User` | 21 | user "User.Eco1" |
| `Eco2.User` | 23 | user "User.Eco2" |
| `Edge.Cuts` | 25 | user |
| `Margin` | 27 | user |
| `F.CrtYd` | 31 | user "F.Courtyard" |
| `B.CrtYd` | 29 | user "B.Courtyard" |
| `F.Fab` | 35 | user |
| `B.Fab` | 33 | user |
| `User.1`–`User.9` | 39–55 odd | user |

For inner copper layers on 4-layer boards: `In1.Cu` = 1 (but note 1 is also `F.Mask` in KiCad 9 — inner layers use the range 16–31 in the standard 4-layer setup; always verify with Board Setup).

---

## Hierarchical sheets

### Top-level sheet block (in `*.kicad_sch`)

```text
(sheet
  (at 30 30)
  (size 60 40)
  (exclude_from_sim no)
  (in_bom yes)
  (on_board yes)
  (dnp no)
  (fields_autoplaced yes)
  (stroke
    (width 0.15)
    (type solid)
  )
  (fill
    (color 255 255 204 0.3)   ← R G B alpha
  )
  (uuid "b1000001-0000-0000-0000-000000000001")
  (property "Sheetname" "RF_Frontend"
    (at 30 27.5 0)
    (effects
      (font (size 1.5 1.5))
      (justify left bottom)
    )
  )
  (property "Sheetfile" "RF_Frontend.kicad_sch"
    (at 30 70.5 0)
    (effects
      (font (size 1.27 1.27) italic)
      (justify left top)
    )
  )
  (pin "NET_NAME" input
    (at 30 45 180)            ← 180 = left side pin (faces left)
    (uuid "e1000001-0000-0000-0000-000000000001")
    (effects
      (font (size 1.27 1.27))
      (justify left)
    )
  )
  (pin "NET_NAME" output
    (at 90 45 0)              ← 0 = right side pin (faces right)
    (uuid "e1000001-0000-0000-0000-000000000002")
    (effects
      (font (size 1.27 1.27))
      (justify right)
    )
  )
)
```

### Pin angle convention

| Pin side | `(at X Y ANGLE)` | Meaning |
| :--- | :--- | :--- |
| Left edge of sheet | `180` | pin stub points left |
| Right edge of sheet | `0` | pin stub points right |
| Top edge of sheet | `270` | pin stub points up |
| Bottom edge of sheet | `90` | pin stub points down |

### Sub-sheet file — `(hierarchical_label ...)`

The sub-sheet `.kicad_sch` must contain matching `(hierarchical_label)` entries for each pin defined in the parent:

```text
(hierarchical_label "NET_NAME"
  (shape input)               ← input / output / bidirectional / passive
  (at 20 50 0)
  (fields_autoplaced yes)
  (effects
    (font (size 1.27 1.27))
    (justify right)
  )
  (uuid "d1000001-0000-0000-0000-000000000001")
)
```

The `shape` in the hierarchical label must match the pin direction in the parent sheet:

| Parent pin type | Label shape |
| :--- | :--- |
| `input` | `input` |
| `output` | `output` |
| `bidirectional` | `bidirectional` |

### `sheet_instances` in sub-sheet

```text
(sheet_instances
  (path "/PARENT_SHEET_UUID/"
    (page "2")
  )
)
```

The path uses the UUID of the parent sheet block (not the file UUID).

---

## Stackup block — `*.kicad_pcb`

This is the single source of truth for EMerge simulation (see `doc/EMerge_export.md`).  
Set in KiCad via **Board Setup → Board Stackup → Physical Stackup**.

```text
(stackup
  (layer "F.SilkS"
    (type "Top Silk Screen")
  )
  (layer "F.Paste"
    (type "Top Solder Paste")
  )
  (layer "F.Mask"
    (type "Top Solder Mask")
    (thickness 0.01)
  )
  (layer "F.Cu"
    (type "copper")
    (thickness 0.035)         ← 1 oz copper = 35 µm
  )
  (layer "dielectric 1"
    (type "core")
    (thickness 1.51)          ← core = total - 2×copper - 2×mask
    (material "FR4")
    (epsilon_r 4.5)           ← dielectric constant
    (loss_tangent 0.02)       ← RF loss at GHz frequencies
  )
  (layer "B.Cu"
    (type "copper")
    (thickness 0.035)
  )
  (layer "B.Mask"
    (type "Bottom Solder Mask")
    (thickness 0.01)
  )
  (layer "B.Paste"
    (type "Bottom Solder Paste")
  )
  (layer "B.SilkS"
    (type "Bottom Silk Screen")
  )
  (copper_finish "ENIG")
  (dielectric_constraints no)
)
```

**JLCPCB 2-layer standard defaults:**

| Parameter | Value |
| :--- | :--- |
| Total thickness | 1.6 mm |
| Copper thickness (1 oz) | 0.035 mm |
| Core dielectric | FR4, εr = 4.5, tanδ = 0.02 |
| Finish | ENIG |

---

## Net definitions — `*.kicad_pcb`

```text
(net 0 "")
(net 1 "GND")
(net 2 "+3V3")
(net 3 "+3V3_A")
(net 4 "RF_IN")
(net 5 "VDET")
(net 6 "USB_DP")
(net 7 "USB_DM")
```

Net 0 is always the unconnected net. Net numbers must be sequential and unique.  
Footprint pads reference nets by number: `(net 1 "GND")`.

---

## Footprint block — `*.kicad_pcb`

```text
(footprint "Library:Footprint_Name"
  (layer "F.Cu")
  (uuid "xxxxxxxx-...")
  (at 50.0 40.0 0)            ← X Y ANGLE — footprint origin
  (property "Reference" "U2"
    (at 0 -2.5 0)             ← relative to footprint origin
    (layer "F.SilkS")
    (uuid "xxxxxxxx-...")
    (effects
      (font (size 1.0 1.0) (thickness 0.15))
    )
  )
  (property "Value" "AD8317"
    (at 0 2.5 0)
    (layer "F.Fab")
    (uuid "xxxxxxxx-...")
    (effects
      (font (size 1.0 1.0) (thickness 0.15))
    )
  )
  (pad "1" smd rect
    (at -1.0 0 0)             ← relative to footprint origin
    (size 0.6 0.5)
    (layers "F.Cu" "F.Paste" "F.Mask")
    (net 4 "RF_IN")
    (uuid "xxxxxxxx-...")
  )
  (pad "2" smd rect
    (at 1.0 0 0)
    (size 0.6 0.5)
    (layers "F.Cu" "F.Paste" "F.Mask")
    (net 1 "GND")
    (uuid "xxxxxxxx-...")
  )
)
```

**Pad types:** `smd` (surface mount), `thru_hole`, `np_thru_hole` (non-plated)  
**Pad shapes:** `rect`, `roundrect`, `circle`, `oval`, `trapezoid`, `custom`

---

## Track and via — `*.kicad_pcb`

```text
;; Copper track
(segment
  (start 50.0 40.0)
  (end 60.0 40.0)
  (width 0.25)
  (layer "F.Cu")
  (net 4)
  (uuid "xxxxxxxx-...")
)

;; Via
(via
  (at 60.0 40.0)
  (size 0.8)                  ← via pad diameter mm
  (drill 0.4)                 ← drill diameter mm
  (layers "F.Cu" "B.Cu")
  (net 4)
  (uuid "xxxxxxxx-...")
)
```

---

## Copper zone — `*.kicad_pcb`

```text
(zone
  (net 1)
  (net_name "GND")
  (layer "F.Cu")
  (uuid "xxxxxxxx-...")
  (hatch edge 0.508)
  (connect_pads
    (clearance 0.2)
  )
  (min_thickness 0.2)
  (filled_areas_thickness no)
  (fill yes
    (thermal_gap 0.3)
    (thermal_bridge_width 0.3)
  )
  (polygon
    (pts
      (xy 0 0) (xy 100 0) (xy 100 80) (xy 0 80)
    )
  )
)
```

---

## Python — read PCB with `pcbnew` (KiCad scripting console)

KiCad ships a Python 3 interpreter with `pcbnew` bindings.  
Run from **KiCad Scripting Console** (PCB editor → Tools → Scripting Console) or:

```bash
"C:\Program Files\KiCad\9.0\bin\python.exe" myscript.py
```

### Load and inspect

```python
import pcbnew

board = pcbnew.LoadBoard(r"C:\ad8317\kicad\ad8317_powermeter.kicad_pcb")

# List all footprints
for fp in board.GetFootprints():
    print(fp.GetReference(), fp.GetX()/1e6, fp.GetY()/1e6, "mm")

# Get a specific footprint
u2 = board.FindFootprintByReference("U2")
print(u2.GetPosition())   # returns VECTOR2I in nm (KiCad internal units)

# Convert: pcbnew uses nanometres internally
x_mm = pcbnew.ToMM(u2.GetX())   # nm -> mm
y_mm = pcbnew.ToMM(u2.GetY())

# List pads of a footprint
for pad in u2.Pads():
    print(pad.GetNumber(), pcbnew.ToMM(pad.GetX()), pcbnew.ToMM(pad.GetY()))

# Get pad absolute position (footprint origin + local pad offset)
pad = list(u2.Pads())[0]
pos = pad.GetPosition()   # absolute in nm
print(pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y))
```

### Modify footprint position

```python
# Move U2 to (50, 40) mm
u2.SetPosition(pcbnew.VECTOR2I(
    pcbnew.FromMM(50.0),
    pcbnew.FromMM(40.0)
))
u2.SetOrientationDegrees(0)

board.Save(board.GetFileName())   # save in-place
# or: pcbnew.SaveBoard("output.kicad_pcb", board)
```

### Add a track

```python
track = pcbnew.PCB_TRACK(board)
track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(50), pcbnew.FromMM(40)))
track.SetEnd  (pcbnew.VECTOR2I(pcbnew.FromMM(60), pcbnew.FromMM(40)))
track.SetWidth(pcbnew.FromMM(0.25))
track.SetLayer(pcbnew.F_Cu)
track.SetNet(board.FindNet("RF_IN"))
board.Add(track)
board.Save(board.GetFileName())
```

### Add a via

```python
via = pcbnew.PCB_VIA(board)
via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(60), pcbnew.FromMM(40)))
via.SetDrillDefault()
via.SetWidth(pcbnew.FromMM(0.8))
via.SetDrill(pcbnew.FromMM(0.4))
via.SetNet(board.FindNet("GND"))
board.Add(via)
board.Save(board.GetFileName())
```

### Add a copper zone (GND pour)

```python
zone = pcbnew.ZONE(board)
zone.SetNet(board.FindNet("GND"))
zone.SetLayer(pcbnew.F_Cu)

outline = zone.Outline()
outline.NewOutline()
for x, y in [(0,0),(100,0),(100,80),(0,80)]:
    outline.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))

zone.SetMinThickness(pcbnew.FromMM(0.2))
zone.SetThermalReliefGap(pcbnew.FromMM(0.3))
zone.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.3))
board.Add(zone)

# Fill all zones
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
board.Save(board.GetFileName())
```

---

## Python — read schematic with `kicad-cli` + regex (no pcbnew)

KiCad 10 deprecated direct `pcbnew` schematic access. Use `kicad-cli` to export,
then parse the resulting files. No external packages needed.

### Export SPICE netlist

```python
import subprocess, pathlib

KICAD_CLI = r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe"
SCH = r"C:\ad8317\kicad\ad8317_powermeter.kicad_sch"
OUT = r"C:\ad8317\fab\board.cir"

subprocess.run([KICAD_CLI, "sch", "export", "netlist",
                "--format", "spice", "--output", OUT, SCH], check=True)
```

### Export BOM (CSV)

```python
subprocess.run([KICAD_CLI, "sch", "export", "bom",
                "--output", r"C:\ad8317\fab\bom.csv",
                "--fields", "Reference,Value,Footprint,MPN,Manufacturer,Qty",
                SCH], check=True)
```

### Export Gerbers

```python
PCB = r"C:\ad8317\kicad\ad8317_powermeter.kicad_pcb"
FAB = r"C:\ad8317\fab"

subprocess.run([KICAD_CLI, "pcb", "export", "gerbers",
                "--output", FAB, PCB], check=True)
subprocess.run([KICAD_CLI, "pcb", "export", "drill",
                "--output", FAB, PCB], check=True)
```

### Read pad positions without pcbnew (pure regex)

```python
import re, math, pathlib

def get_pad_positions(kicad_pcb_path):
    """
    Returns {"REF:pad": (x_mm, y_mm)} for every pad.
    Reads the .kicad_pcb file directly — no pcbnew dependency.
    """
    pads = {}
    content = pathlib.Path(kicad_pcb_path).read_text()

    for fp_block in re.finditer(
            r'\(footprint\s.*?(?=\n\s*\(footprint|\Z)', content, re.DOTALL):
        ref_m = re.search(r'\(reference\s+"([^"]+)"', fp_block.group())
        at_m  = re.search(r'^\s*\(at\s+([\d.\-]+)\s+([\d.\-]+)(?:\s+([\d.\-]+))?\)',
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
            ax = fp_x + lx * math.cos(fp_a) - ly * math.sin(fp_a)
            ay = fp_y + lx * math.sin(fp_a) + ly * math.cos(fp_a)
            pads[f"{ref}:{pad_m.group(1)}"] = (ax, ay)
    return pads

pads = get_pad_positions(r"C:\ad8317\kicad\ad8317_powermeter.kicad_pcb")
print(pads["J1:1"])    # (x_mm, y_mm) of SMA pad 1
print(pads["U2:3"])    # (x_mm, y_mm) of AD8317 pad 3
```

### Read stackup from `.kicad_pcb`

```python
def read_stackup(kicad_pcb_path):
    content = pathlib.Path(kicad_pcb_path).read_text()
    m = re.search(r'\(stackup(.*?)\)\s*\(', content, re.DOTALL)
    if not m:
        return {'board_thickness_mm': 1.6, 'copper_thickness_mm': 0.035,
                'er': 4.5, 'tand': 0.02, 'copper_layers': 2}
    block = m.group(1)
    layers = []
    for lm in re.finditer(r'\(layer\s+"([^"]+)"(.*?)(?=\(layer|\Z)', block, re.DOTALL):
        type_m  = re.search(r'\(type\s+"?([^"\)]+)"?\)', lm.group(2))
        thick_m = re.search(r'\(thickness\s+([\d.]+)\)', lm.group(2))
        er_m    = re.search(r'\(epsilon_r\s+([\d.]+)\)', lm.group(2))
        tand_m  = re.search(r'\(loss_tangent\s+([\d.]+)\)', lm.group(2))
        layers.append({
            'name':  lm.group(1),
            'type':  type_m.group(1).strip() if type_m else '',
            'thick': float(thick_m.group(1)) if thick_m else 0,
            'er':    float(er_m.group(1))    if er_m    else None,
            'tand':  float(tand_m.group(1))  if tand_m  else None,
        })
    core = next((l for l in layers if l['type'] in ('core','dielectric','prepreg')
                 and l['er']), None)
    copper = [l for l in layers if l['type'] == 'copper']
    return {
        'board_thickness_mm':  sum(l['thick'] for l in layers),
        'copper_thickness_mm': max((l['thick'] for l in copper), default=0.035),
        'er':    core['er']   if core else 4.5,
        'tand':  core['tand'] if core else 0.02,
        'copper_layers': len(copper),
    }
```

---

## `kicad-cli` reference

All commands require KiCad 9.  
Path: `C:\Program Files\KiCad\9.0\bin\kicad-cli.exe`

### Schematic commands

```powershell
# Export Gerbers
kicad-cli sch export netlist --format spice --output fab\board.cir board.kicad_sch
kicad-cli sch export bom --output fab\bom.csv board.kicad_sch
kicad-cli sch export svg --output fab\sch.svg board.kicad_sch
kicad-cli sch export pdf --output fab\sch.pdf board.kicad_sch

# Run ERC (Electrical Rules Check) — outputs report
kicad-cli sch erc --output fab\erc.txt board.kicad_sch
```

### PCB commands

```powershell
# Manufacturing outputs
kicad-cli pcb export gerbers --output fab\ board.kicad_pcb
kicad-cli pcb export drill --output fab\ board.kicad_pcb
kicad-cli pcb export pos --output fab\components.csv --format csv board.kicad_pcb
kicad-cli pcb export step --output fab\board.step board.kicad_pcb

# Render
kicad-cli pcb export svg --output fab\pcb.svg --layers F.Cu,B.Cu,Edge.Cuts board.kicad_pcb
kicad-cli pcb export pdf --output fab\pcb.pdf board.kicad_pcb

# Run DRC
kicad-cli pcb drc --output fab\drc.txt board.kicad_pcb
```

---

## Direct file editing — safe workflow

When editing `.kicad_sch` or `.kicad_pcb` by hand or script:

1. **Close KiCad first** — KiCad overwrites the file on save and will discard external changes.
2. **Make a backup** before editing:

   ```powershell
   Copy-Item ad8317_powermeter.kicad_pcb ad8317_powermeter.kicad_pcb.bak
   ```

3. **Edit the file** (script or text editor).
4. **Validate** by opening in KiCad — any parse error shows file + line + offset.
5. **KiCad will reformat** the file on first save (normalises whitespace, may reorder some fields) — this is expected and safe.

### Common parse errors and fixes

| Error message | Cause | Fix |
| :--- | :--- | :--- |
| `need a number for 'text angle'` | `(at X Y)` missing angle | Add `0`: `(at X Y 0)` |
| `Expecting 'bitmap, bus, ...'` | Unknown or misspelled token | Remove the invalid line |
| `Expecting ')'` | Mismatched parentheses | Count `(` and `)` in the block |
| `need a positive number` | Negative size or zero width | Check `(size W H)` and `(width N)` |
| `Unknown token` | Inline `effects` instead of expanded | Expand `effects` to multi-line block |

### UUID generation (Python)

Every new object in a KiCad file needs a unique UUID:

```python
import uuid
print(str(uuid.uuid4()))   # e.g. 7f3a1b2c-4d5e-6f70-8192-a3b4c5d6e7f8
```

For batch generation:

```python
# Generate 20 UUIDs for new footprint pads
for _ in range(20):
    print(str(uuid.uuid4()))
```

---

## Project library paths

Both tables use `${KIPRJMOD}` which resolves to the directory containing the `.kicad_pro` file.

### `sym-lib-table` (symbol library)

```text
(sym_lib_table
  (version 7)
  (lib
    (name "ad8317_custom")
    (type "KiCad")
    (uri "${KIPRJMOD}/../lib/symbols/ad8317_custom.kicad_sym")
    (options "")
    (descr "Custom symbols for ad8317_powermeter")
  )
)
```

### `fp-lib-table` (footprint library)

```text
(fp_lib_table
  (version 1)
  (lib
    (name "ad8317_custom")
    (type "KiCad")
    (uri "${KIPRJMOD}/../lib/footprints/ad8317_custom.pretty")
    (options "")
    (descr "Custom footprints for ad8317_powermeter")
  )
)
```

### Available path variables

| Variable | Resolves to |
| :--- | :--- |
| `${KIPRJMOD}` | Directory of the current `.kicad_pro` file |
| `${KICAD9_FOOTPRINT_DIR}` | KiCad 9 built-in footprint libraries |
| `${KICAD9_SYMBOL_DIR}` | KiCad 9 built-in symbol libraries |
| `${KICAD_USER_TEMPLATE_DIR}` | User template directory |

---

## Custom DRC rules — `*.kicad_dru`

```text
(version 1)

(rule "Min track width"
  (constraint track_width (min 0.127mm))
)

(rule "Min via drill"
  (constraint hole_size (min 0.3mm))
)

(rule "RF net — wider tracks"
  (condition "A.NetName == 'RF_IN'")
  (constraint track_width (min 0.5mm))
)

(rule "SMA keepout"
  (condition "A.Type == 'Zone' && A.NetName == 'RF_IN'")
  (constraint clearance (min 0.5mm))
)
```

Run DRC from PCB editor or via CLI:

```powershell
kicad-cli pcb drc --output drc_report.txt ad8317_powermeter.kicad_pcb
```

---

## Creating a custom symbol — `*.kicad_sym`

Reference format taken from the KiCad 9 built-in library `RF_Amplifier.kicad_sym` (AD8313xRM).  
Use this as the authoritative template — not the symbol editor export from older KiCad versions.

### File header

```text
(kicad_symbol_lib
    (version 20241209)
    (generator "kicad_symbol_editor")
    (generator_version "9.0")
    ...one (symbol ...) block per component...
)
```

### Symbol block structure

```text
(symbol "PART_NAME"
    (exclude_from_sim no)
    (in_bom yes)
    (on_board yes)

    ;; --- visible properties (no hide) ---
    (property "Reference" "U"
        (at 5.08 13.97 0)
        (effects (font (size 1.27 1.27)))
    )
    (property "Value" "PART_NAME"
        (at 7.62 11.43 0)
        (effects (font (size 1.27 1.27)))
    )

    ;; --- hidden properties ---
    (property "Footprint" "Library:Footprint_Name"
        (at 0 -2.54 0)
        (effects (font (size 1.27 1.27)) (hide yes))
    )
    (property "Datasheet" "https://..."
        (at 0 -2.54 0)
        (effects (font (size 1.27 1.27)) (hide yes))
    )
    (property "Description" "One-line description"
        (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes))
    )
    (property "ki_keywords" "keyword1 keyword2"
        (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes))
    )
    (property "ki_fp_filters" "Footprint*Pattern*"
        (at 0 0 0)
        (effects (font (size 1.27 1.27)) (hide yes))
    )

    ;; --- body in _0_1, pins in _1_1 ---
    (symbol "PART_NAME_0_1"
        (rectangle
            (start -10.16 10.16)
            (end 10.16 -10.16)
            (stroke (width 0.254) (type default))
            (fill (type background))
        )
    )
    (symbol "PART_NAME_1_1"
        ...pins...
    )
    (embedded_fonts no)
)
```

**Rules:**
- Body graphics (rectangle, lines, arcs) go in `_0_1`.
- All pins go in `_1_1`.
- `embedded_fonts no` is required inside the symbol block (not just at file level).
- Do **not** add a `(pin_names ...)` block unless you need to hide or offset pin name labels.
- `ki_keywords` and `ki_fp_filters` are required for the symbol chooser search to work.

### Pin syntax

```text
(pin TYPE SHAPE
    (at X Y ANGLE)
    (length 2.54)
    (name "PIN_NAME"
        (effects (font (size 1.27 1.27)))
    )
    (number "PAD_NUMBER"
        (effects (font (size 1.27 1.27)))
    )
)
```

**Valid TYPE values:** `input`, `output`, `bidirectional`, `tristate`, `passive`,
`unspecified`, `power_in`, `power_out`, `open_collector`, `open_emitter`, `no_connect`

**Valid SHAPE values:** `line`, `inverted`, `clock`, `inverted_clock`, `input_low`,
`clock_low`, `output_low`, `edge_clock_high`, `non_logic`

Use `line` for most pins. Do **not** use `no_connect` as a shape — it is not a valid shape token.

### Pin placement angles

| Pin location | `(at X Y ANGLE)` | Wire exits toward |
| :--- | :--- | :--- |
| Left side | `(-12.7 Y 0)` | left |
| Right side | `(12.7 Y 180)` | right |
| Top | `(X 12.7 270)` | up |
| Bottom | `(X -12.7 90)` | down |

Pin `(at ...)` is the **wire endpoint** (where you connect a wire in the schematic).  
The pin body draws inward by `length` units toward the symbol body.

### Handling duplicate pins (e.g. multiple GND/COMM pads)

Spread them at different X positions along the same edge — do **not** stack them at
identical coordinates (causes render issues in some KiCad versions):

```text
;; AD8317: pins 7, 8 = COMM, 9 = EP — all GND, bottom edge
(pin power_in line (at -2.54 -12.7 90) (length 2.54)
    (name "COMM" ...) (number "7" ...))
(pin power_in line (at  2.54 -12.7 90) (length 2.54)
    (name "COMM" ...) (number "8" ...))
(pin power_in line (at  0    -12.7 90) (length 2.54)
    (name "~{EP}" ...) (number "9" ...))
```

Same pattern used by AD8313 for its dual VPOS pins on the top edge.

### Overline / tilde notation

`~{TEXT}` renders with an overline bar (active-low signals):

```text
(name "~{EP}"  ...)   →  EP̄  (overline)
(name "~{OE}"  ...)   →  OE̅
```

### Complete AD8317 example

```text
(kicad_symbol_lib
    (version 20241209)
    (generator "kicad_symbol_editor")
    (generator_version "9.0")
    (symbol "AD8317"
        (exclude_from_sim no)
        (in_bom yes)
        (on_board yes)
        (property "Reference" "U"
            (at 5.08 13.97 0)
            (effects (font (size 1.27 1.27)))
        )
        (property "Value" "AD8317"
            (at 7.62 11.43 0)
            (effects (font (size 1.27 1.27)))
        )
        (property "Footprint" "Package_DFN_QFN:LFCSP-8-1EP_2x2mm_P0.5mm_EP0.65x0.65mm"
            (at 0 -2.54 0)
            (effects (font (size 1.27 1.27)) (hide yes))
        )
        (property "Datasheet" "https://www.analog.com/media/en/technical-documentation/data-sheets/AD8317.pdf"
            (at 0 -2.54 0)
            (effects (font (size 1.27 1.27)) (hide yes))
        )
        (property "Description" "1 MHz to 10 GHz, 55 dB Log Detector/Controller, LFCSP-8"
            (at 0 0 0)
            (effects (font (size 1.27 1.27)) (hide yes))
        )
        (property "ki_keywords" "RF LOG POWER DETECTOR 10GHz"
            (at 0 0 0)
            (effects (font (size 1.27 1.27)) (hide yes))
        )
        (property "ki_fp_filters" "LFCSP*2x2mm*P0.5mm*"
            (at 0 0 0)
            (effects (font (size 1.27 1.27)) (hide yes))
        )
        (symbol "AD8317_0_1"
            (rectangle
                (start -10.16 10.16)
                (end 10.16 -10.16)
                (stroke (width 0.254) (type default))
                (fill (type background))
            )
        )
        (symbol "AD8317_1_1"
            (pin input line (at -12.7  5.08 0)   (length 2.54) (name "INHI"  ...) (number "1" ...))
            (pin input line (at -12.7  2.54 0)   (length 2.54) (name "INLO"  ...) (number "2" ...))
            (pin power_in line (at 0   12.7 270) (length 2.54) (name "VPOS"  ...) (number "3" ...))
            (pin output line (at 12.7  2.54 180) (length 2.54) (name "CLPF"  ...) (number "4" ...))
            (pin output line (at 12.7  5.08 180) (length 2.54) (name "VOUT"  ...) (number "5" ...))
            (pin input line  (at 12.7 -2.54 180) (length 2.54) (name "ENBL"  ...) (number "6" ...))
            (pin power_in line (at -2.54 -12.7 90) (length 2.54) (name "COMM"   ...) (number "7" ...))
            (pin power_in line (at  2.54 -12.7 90) (length 2.54) (name "COMM"   ...) (number "8" ...))
            (pin power_in line (at  0    -12.7 90) (length 2.54) (name "~{EP}"  ...) (number "9" ...))
        )
        (embedded_fonts no)
    )
)
```

### Registering the library

Add an entry to `kicad/sym-lib-table`:

```text
(sym_lib_table
    (version 7)
    (lib
        (name "ad8317_custom")
        (type "KiCad")
        (uri "${KIPRJMOD}/../lib/symbols/ad8317_custom.kicad_sym")
        (options "")
        (descr "Custom symbols for ad8317_powermeter")
    )
)
```

`${KIPRJMOD}` resolves to the directory containing the `.kicad_pro` file.  
Reload via **Preferences → Manage Symbol Libraries** after editing the file on disk.

### Common errors and fixes

| Error | Cause | Fix |
| :--- | :--- | :--- |
| `Errors loading symbols` / `Expecting 'line, inverted, ...'` | `no_connect` used as pin shape | Change shape to `line` |
| Symbol loads but body is invisible | `_0_1` / `_1_1` sub-symbol names don't match parent name | Names must be `PARTNAME_0_1` / `PARTNAME_1_1` |
| Symbol not shown in chooser | Missing `ki_keywords` or library not in `sym-lib-table` | Add both; reload library table |
| Pins render on wrong side | Angle wrong — e.g. left-side pin needs `0` not `180` | `(at -12.7 Y 0)` for left, `(at 12.7 Y 180)` for right |
| Duplicate pins cause render glitch | Two pins at identical `(at X Y ANGLE)` | Spread at −2.54 / 0 / +2.54 along the edge |

---

## ad8317_powermeter file map

```text
kicad/
├── ad8317_powermeter.kicad_pro    project settings, net classes (Default + RF)
├── ad8317_powermeter.kicad_sch    top-level: 4 hierarchical sub-sheets
├── RF_Frontend.kicad_sch          SMA J1, 30dB atten, AD8317 U2, RC filter
├── MCU.kicad_sch                  STM32F373 U1, REF3030 U3, all MCU peripherals
├── Power_Management.kicad_sch     charger U6, LDOs U4/U5, MOSFETs, protection
├── User_Interface.kicad_sch       OLED DISP1, buttons SW1/SW2, USB-C CONN1
├── ad8317_powermeter.kicad_pcb    2-layer FR4 1.6mm PCB, KiCad 9 stackup
├── ad8317_powermeter.kicad_dru    JLCPCB DRC rules + RF_IN min 0.5mm
├── sym-lib-table                  → ../lib/symbols/ad8317_custom.kicad_sym
└── fp-lib-table                   → ../lib/footprints/ad8317_custom.pretty/

lib/
├── symbols/ad8317_custom.kicad_sym   empty — add custom symbols here
├── footprints/ad8317_custom.pretty/  empty — add custom footprints here
└── *.stp / *.a3dcomp                 3D models (SMA connector etc.)

fab/                               (generated by 01_export_kicad.py)
├── *-F_Cu.gbr                     top copper Gerber
├── *-B_Cu.gbr                     bottom copper Gerber
├── *.drl                          Excellon drill file
└── board.cir                      SPICE netlist (R/L/C values)
```
