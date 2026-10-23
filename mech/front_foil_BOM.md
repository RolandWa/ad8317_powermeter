# Front foil for Hammond 1590A lid - files, BOM and procedure

## Files

| File | Purpose |
|------|---------|
| `front_foil_1590A.dxf` | Editable CAD file (mm, 1:1). A4 sheet with 12 foils as block `FOIL`. Layers: `FOIL_CUT`, `HOLES_CUT` (display window only), `BUTTON_MARK` (button circles, printed outline only, NOT cut), `ART_BAND`, `ART_TEXT`, `MARKS`, `SHEET`, `NOTES` |
| `front_foil_1590A_print_A4.pdf` | Colour laser print sheet, A4 portrait, 1:1 (12 foils, spares for cutting mistakes) |
| `front_foil_1590A_test_sheet_A4.pdf` | Plain-paper test page: find out which way the manual tray prints (TOP arrow, printed-side marker, 100 mm scale bar) |
| `gen_front_foil.py` | Generator of all of the above. Edit the parameters at the top, run `python gen_front_foil.py` (needs `ezdxf`, `reportlab`) |

## Geometry (foil coordinates, origin = lid centre, viewed from the top)

| Item | Value |
|------|-------|
| Foil outline | 87.0 x 33.0 mm, R5 (lid top flat face is 88.2 x 34.0 mm, taken from `1590A.stp`) |
| "+ NEXT" lever post (SW2), upper | printed circle dia 5.0 mm (not cut) at x +21.5, y +5.0 (y down) |
| "- MENU" lever post (SW1), lower | printed circle dia 5.0 mm (not cut) at x +21.5, y +11.8 |
| SBC-OLED01 window | 22.4 x 11.5 mm at x +3.6, y +2.8 (**assumed**, see below) |

Positions come from the KiCad PCB (board centre 177.4 / 84.8 mm; SW1 192.5 / 96.6; SW2 180.5 / 96.6; J3 display socket 184.8 / 71.9).

**Open points - verify before cutting the real foil and the lid**

1. The Joy-IT SBC-OLED01 datasheet gives only 27 x 27 x 11 mm, no active-area or hole data.
   The window is derived from typical 0.96" SSD1306 modules with the module turned so the pin row is at the top (display image must be flipped in firmware). Measure the real module (active area size and its distance from the pin edge) and edit `WIN_W`, `WIN_H`, `WIN_C`.
2. The module (27 mm square, hanging below the J3 socket) reaches y = +13 mm, i.e. it lies over SW1/SW2. The window and button circles only match if the module is high enough above the switches - check in the assembled stack.
3. RF input limits printed under RF IN: range -55...0 dBm, sens. -55 dBm and max +12 dBm come from the AD8317 data (`doc/ADC_Signal_Path_Analysis.md`, `doc/Lab_Optimization_DC_to_6GHz.md`); "Max DC: 50 V" is the rating of the DC-block capacitors C1/C2 (confirmed by the user). The 50 ohm termination R1 || R2 (2 x 100R 0402, assuming standard 1/16 W = 62.5 mW parts, no MPN in the schematic) tolerates 125 mW = +21 dBm continuous (about +18 dBm with 50 % derating), so the AD8317 (+12 dBm absolute maximum) remains the limit. Resistor working voltage (typ. 50 V for 0402) is not limiting either (+12 dBm = 0.89 V rms).
4. Lid and end walls are machined to `hammond_1590A_machined.step` / `cnc_lid_top.dxf` / `cnc_wall_left_SMA.dxf` / `cnc_wall_right_USB.dxf`: 2 x dia 4.2 post holes, window 23.4 x 12.5, SMA hole dia 6.8, USB-C opening 12.6 x 6.6. The foil over the post holes is not cut: the dome-topped posts press against it and it bulges ~0.2 mm.
5. SMA: in the CAD (J1 Wurth 60312202114307) the barrel tip is 5.95 mm beyond the PCB edge, i.e. it protrudes ~3.7 mm outside the case wall, not recessed (USB-C is recessed 2.7 mm). The hole is a plain dia 6.8 through hole; if the connector should be recessed, move it on the PCB or tell me and I add a counterbore.
6. Module height (glass top vs. lid underside 10.94 mm) is assumed from the 11 mm datasheet height with full insertion in the 8.5 mm socket; the glass should just touch the lid underside. Lever post height (`DOME_TOP`, shoulder) is tuned in 0.1 mm steps after a test print.

## Levers (mech/levers, `gen_levers.py`)

SW1/SW2 are covered by the OLED module, so each switch drives a printed lever (stacked, pivot at x = -13 outside the module, passing under the module) with a push post through the lid: SW2 -> NEXT "+" post at (21.5, -5.0) (upper on the foil), SW1 -> MENU "-" post at (21.5, -11.8) (lower) (x right, y up). Posts have a domed top (R8, 0.2 mm above lid) and a shoulder under the lid as upper stop; the lever feet rest on the switch actuators (top at 3.1 mm).

- **NEXT lever** (lower layer, foot on SW2): straight arm to x = 17.5, then an angled arm to the upper post. The bend is built from overlapping rectangles plus a full disc at the joint (single solid, no gaps, checked by 0.5 mm cross-sections along its length).
- **MENU lever** (upper layer, foot on SW1): plain straight flat arm (no ribs, easy to print) to the lower post; its foot goes through a slot in the NEXT lever. Arm thicknesses: NEXT 2.3 mm (z 3.5..5.8), MENU 1.8 mm (z 6.2..8.0).
- **Axles**: printed pegs dia 1.6 on the lever sides (0.9 mm long, chamfered), no steel pins. The bracket is an inverted U (pad bonded under the lid + two 0.8 mm ears). The ears have an open-bottom slot (neck 1.5 mm, a 0.05 mm per side snap) and dia 1.8 axle holes, so the levers are simply pushed up into the bracket from below during assembly; the ears can spring apart sideways if needed. Lever 1 and 2 stay captured by the switch (spring) pushing them up against the post shoulders.
- Narrow pivot end (4.6 mm) keeps the ears inside the lid cavity (|y| < 15.7 mm).

Stiffness (beam model, switch force 2.55 N worst case = B3FS variant, assumed; finger force at post = 2.1 N MENU, 1.2 N NEXT; section stress max 3.5 MPa, far below yield): lost travel at the post because of lever bending / total post stroke:

| Material | MENU | NEXT |
|----------|------|------|
| PETG (E 1.5 GPa) | 0.19 of 0.50 mm | 0.64 of 1.23 mm |
| PETG-CF / PA12 (2.5 GPa) | 0.12 of 0.42 mm | 0.38 of 0.98 mm |
| PA-CF (6 GPa) | 0.05 of 0.35 mm | 0.16 of 0.76 mm |

The NEXT lever is the softer one (longest, angled path, no ribs for easy printing): use PETG-CF / PA12-CF for it if the button feels soft; PETG works but loses about half of the post travel. Print flat (arm plane on the bed), 0.1-0.15 mm layers, 100 % infill or 5+ walls.

| File | Part |
|------|------|
| `levers/lever_menu_SW1.step/.stl`, `levers/lever_next_SW2.step/.stl` | The two levers with pegs and posts |
| `levers/lever_bracket.step/.stl` | Fixture (inverted U) bonded to the lid underside |
| `levers/assembly_check.step` | Case + levers + bracket + OLED envelope for visual check |
| `hammond_1590A_machined.step` | Case with the lid/wall cut-outs (3D reference for the CNC files) |

Automatic checks (`gen_levers.py` with a `kicad-cli pcb export step` board): no interference between levers, bracket, case, OLED envelope and all PCB components (the only overlap is the intentional 0.05 mm snap neck on the pegs).

## CNC files (mech/cnc_*.dxf, `gen_cnc_dxf.py`)

Machine frame: X right, Y away from the operator, Z up (spindle). One DXF per set-up; the face to machine is up, work zero X0 Y0 = lower-left corner of the part bounding box (as seen from the spindle), Z0 = the machined outer surface, cuts to negative Z. Annotations are on the right of each part, geometry only on layers `STOCK` (bounding box) and `CUT_THROUGH`, `CENTER` marks, `AXES` shows X/Y.

| File | Set-up | Features (X, Y of centres) |
|------|--------|----------------------------|
| `cnc_lid_top.dxf` | lid, top face up, 92.3 x 38.1 | OLED window 23.4 x 12.5 R1 at (49.75, 16.25); NEXT (+) post dia 4.2 at (67.65, 14.05); MENU (-) post dia 4.2 at (67.65, 7.25); depth Z-2.2 |
| `cnc_wall_left_SMA.dxf` | left end wall, outside up, 38.5 x 27.0 | SMA hole dia 6.8 at (19.41, 18.56), depth Z-2.5; the connector barrel protrudes ~3.7 mm out of the wall |
| `cnc_wall_right_USB.dxf` | right end wall, outside up | USB-C opening 12.6 x 6.6 full radius at (19.15, 19.25), depth Z-2.5; receptacle recessed 2.7 mm |

Datum edges are the case bounding box from the CAD (lid +-46.15 x +-19.05, base width +-19.25); re-touch the real part edges before cutting. The lid window needs a cutter of dia <= 2.0 mm for its R1 corners.

### Tools and cutting data

Starting values for the die-cast aluminium Hammond 1590A (alloy not specified, treated like A380: abrasive, chips weld easily). Verify on a scrap piece first and lower the feed if the spindle or the machine is not rigid. Chip load is the invariant: feed F = rpm x flutes x fz.

| Operation | Cutter | rpm | Feed F | Plunge / ramp | Depth per pass ap | Radial step ae |
|-----------|--------|-----|--------|---------------|-------------------|----------------|
| Lid: OLED window 23.4 x 12.5 R1, 2 x dia 4.2 post holes (Z-2.2) | dia 2.0 mm, 2 flutes, solid carbide, uncoated or ZrN, polished flutes, high helix (35-45 deg) "for aluminium", cutting length >= 4 mm | 12 000 | 300 mm/min (fz 0.012) | helical ramp 2-3 deg at 100 mm/min, never plunge straight in | 1.1 mm (2 passes to Z-2.2) | 0.8 mm (0.4 x D); finish pass 0.1 mm |
| End walls: SMA dia 6.8, USB-C 12.6 x 6.6 R3.3 (Z-2.5) | dia 3.0 mm, 2 flutes, same type, cutting length >= 5 mm | 12 000 | 400 mm/min (fz 0.017) | helical ramp 2-3 deg at 150 mm/min | 1.25 mm (2 passes to Z-2.5) | 1.2 mm (0.4 x D); finish pass 0.1 mm |

- With a spindle that reaches 24 000 rpm double the feeds (600 and 800 mm/min); on a small hobby machine (< 10 000 rpm) lower rpm and feed proportionally.
- The dia 2.0 mm cutter does the whole lid; it is also the only one that can make the R1 window corners (cutter dia <= 2.0). The dia 3.0 mm cutter is only a speed-up for the walls; the dia 2.0 mm cutter can machine all three set-ups (use ap 1.0, F 300 mm/min).
- Window and USB-C opening: clear the whole pocket (no contour that leaves a slug, which jams the cutter). Finish contour with climb milling, 0.1 mm radial, same feed. The dia 4.2 and dia 6.8 holes are helical interpolation with the cutter above (circle toolpath, not a drill).
- Cutting fluid: air blast plus a few drops of aluminium cutting oil / isopropyl alcohol mist; keep chips cleared to avoid re-cutting.
- Work holding: lid and wall faces up on a flat sacrificial plate (3 mm aluminium or MDF) so Z-2.2 / Z-2.5 breaks through into it and not into the vise; support the lid under the window and holes, clamp on the flange / bosses, not on the thin plate.
- Z0 = the machined outer surface, probe it after clamping; the lid plate is 2.0 mm thick and the end walls 2.2 mm.

## BOM

| # | Item | Qty | Note |
|---|------|-----|------|
| 1 | Avery Zweckform L4775-8 polyester label film, white, A4 (210 x 297 mm), laser | 1 sheet (8 in pack) | Conrad 1594847; colour laser compatible, 1 sheet = 12 foils |
| 2 | Colour laser printer (A4, manual / bypass tray) | 1 | Plain paper for test sheet |
| 3 | Plain A4 paper | 2 | Test sheet, scale check |
| 4 | Utility knife ("tapeta") with fresh blade | 1 | Outline cut |
| 5 | Steel ruler (min 100 mm) + cutting mat | 1 + 1 | |
| 6 | Tweezers, isopropyl alcohol + lint-free wipe | 1 | Clean the lid before sticking |
| 7 | Optional: clear self-adhesive protective overlay, A4 | 1 | Scratch protection of the printed foil |
| 8 | Hammond 1590A (lid + base), machined per `cnc_lid_top.dxf` / `cnc_wall_left_SMA.dxf` / `cnc_wall_right_USB.dxf` | 1 | Lid window + 2 post holes, SMA hole, USB-C opening (open point 4) |
| 9 | 3D-printed levers x2 + bracket x1 (PETG / nylon) | 1 set | `mech/levers/*.stl` |
| 10 | Solid-carbide end mill dia 2.0 mm, 2 flutes, for aluminium (uncoated or ZrN) | 2 | Lid; also usable for the walls (spare) |
| 11 | Solid-carbide end mill dia 3.0 mm, 2 flutes, for aluminium | 1 | End-wall openings (optional speed-up) |
| 12 | Sacrificial plate 3 mm (aluminium or MDF), clamps / vise soft jaws, aluminium cutting oil | 1 set | Work holding and breakthrough protection |
| 13 | Epoxy or thin double-sided tape (VHB) | - | Bond bracket pad to lid underside (no holes visible through the foil) |

## Procedure

1. Print `front_foil_1590A_test_sheet_A4.pdf` on plain paper following the page instructions; note how to load the manual tray (which face, which edge first).
2. Print `front_foil_1590A_print_A4.pdf` at 100 % (no "fit to page"), colour; check that the bar measures 100 mm.
3. Cut along the outer outline with the knife and ruler (the colour bleeds 1 mm past the cut line; the corner marks show the outline). Cut only the display window (knife and ruler); leave the two button circles uncut.
4. Clean the lid, peel the backing, align on the lid, press from the centre outwards.
