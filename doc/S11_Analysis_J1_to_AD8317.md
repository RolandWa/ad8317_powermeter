# S11 analysis: J1 (SMA pad) → AD8317 input, 1 MHz – 10 GHz

Date: 2026-10-09. Board: Würth 60312202114307, 4-layer FR-4 (εr 4.5, tanδ 0.02), In1.Cu GND reference at 0.533 mm.
Ports: **P1** = J1 pad, **P2** = U1:1 (INHI), **P3** = U1:8 (INLO).
Everything below is reproducible with `sim/qucs/run_s11_study.py` (qucsator 0.0.19, numpy).

## 1. AD8317 input model (replaces the 50 Ω terminations of P2/P3)

`sim/qucs/ad8317_input_model.py` → `AD8317_INHI_1MHz_10GHz.s1p` (1-port, INHI vs ground) and
`AD8317_INHI_INLO_series_1MHz_10GHz.s2p` (series element between P2 and P3, used in the Qucs files).

| f | Zin (datasheet Rev. D) |
|---|---|
| LF (p.10) | 500 Ω ‖ 0.7 pF |
| 0.9 / 1.9 / 2.2 GHz | 1500 ‖ 0.33 / 950 ‖ 0.38 / 810 ‖ 0.39 (Ω ‖ pF) |
| 3.6 / 5.8 / 8.0 GHz | 300 ‖ 0.33 / 110 ‖ 0.05 / 28 ‖ 0.79 (Ω ‖ pF) |

G = 1/R and C are interpolated vs log f between anchors; at 10 GHz the Figure-15 chart point is used (`variant="table"`; `hold` and `chart` variants exist, see `ad8317_input_model.py`). The chip is **not** 50 Ω:
alone it reflects −0.6 … −3 dB over most of the band (|S11| ≈ −11 dB only around 6 GHz). INLO is an ac-coupled RF common, so
the model is a two-terminal load INHI–INLO (INLO returns to GND through C2 on the board).
Existing Figure-15-based files (`sim/AD8317_INHI_S11_*.s1p`, 0.05–10 GHz) were not used: they are chart readings, and the numeric table above is what the datasheet states.

## 2. Result 1 — the rfsim 3-port cannot answer the question

Qucs case **A1** = `rfsim_J1_U1_3port_1MHz_10GHz.s3p` (run_20261009_115836, fine mesh) + AD8317 load.
It is indistinguishable from **A0** (same 3-port with 50 Ω on P2/P3): 

| | 1 MHz–1 GHz | 3 GHz | 6 GHz | 10 GHz |
|---|---|---|---|---|
| A0 / A1 \|S11\| | −0.2 … −0.4 dB | −1.7 dB | −5.8 dB | −11.5 dB |

Because the load does not matter, P1 is electrically isolated from P2/P3 in the FDTD result: S21 = −103 dB, S22 = −0.05 dB at 1 GHz.
With R1/R2 = 100 Ω to GND and C1 = 47 nF in series, a real board gives S11 ≈ −10…−25 dB and S21 ≈ 0 dB at 1 GHz.
Evidence that this is a model/solver artefact and not the layout:

* S21 at 1 GHz gets *worse* with mesh refinement: coarse −48 dB → medium −77 dB → fine −103 dB (runs 20261007_163618 / 20261007_164359 / 20261009_*).
* The run with `lumped=False` (gap between pads left open) has a *higher* S21 (−31 dB) than any run with lumped C1/R1/R2 (−48 … −108 dB), so the lumped parts are not conducting.
* Layout plot of model.json shows R1/R2 bridging trace → F.Cu GND pour and C1/C2 bridging the pad gaps, i.e. the geometry itself is as in the schematic.
* Below ~100 MHz FDTD is not trustworthy anyway (not covered by the plan either); the s3p was extended flat from 10 MHz to 1 MHz only so Qucs can sweep from 1 MHz.

So the EM numbers (A0/A1) say only "open SMA pad, 0.3 pF" and must not be used to judge the match.

Follow-up tests on the same model (coarse mesh, port 1 only, 0.1–10 GHz; scratch runs, not stored):

| Run | S11 / S21 @ 1 GHz |
|---|---|
| T0 baseline (R1/R2/C1/C2 lumped) | −0.35 / −78 dB |
| T1 C1,C2 → metal shorts, R1/R2 lumped | −0.35 / −96 dB (same as T0) |
| T2 C1,C2 → shorts, R1/R2 removed | −0.22 / −51 dB |
| T4 R1/R2 removed, C1,C2 = 0.2 pF | −0.22 / −51 dB (same as T2) |
| T6 as T0, lumped boxes 0.2 mm tall in z (patched mesh) | −0.36 / −78 dB (same as T0) |
| T3 as T1 but R1/R2 = 100 kΩ | energy = NaN from the first time steps |

* Hypothesis "R1/R2 or C1/C2 not touching copper" is rejected: point-in-polygon check on model.json shows both box faces of every element inside the correct F.Cu polygon (signal island: P1, R1/R2 near ends, C1 left pad; GND pour with 64 vias: R1/R2 far ends, C2; C1 right pad = island with P2).
* Hypothesis "zero-thickness box in z" is not confirmed: T6 gives the same S-parameters.
* What the tests do show: the element *value* does not act as set (C = 0.2 pF behaves like a short, T4 = T2; R = 100 Ω like a low-impedance shunt, T0 vs T2). Even with C shorted and R removed S21 stays at −51 dB at 1 GHz, so the problem is not only in the elements.

### Root cause found: ports reference a plane that does not exist under them

Upstream validation (`run_lumped.py`, `run_rlc.py`, `run_shunt.py`, run here with the same openEMS rc2) **all pass** (series R −4.2/−3.5 dB, series R/L/C vs theory, shunt-C notch ESL within 3 %), so solver and element handling are fine. The difference is the board:

* In1.Cu and In2.Cu are **void under the whole RF input by design** (intentional, to reduce the SMA pad capacitance; GND only on F.Cu pour and B.Cu) (a ~4 mm wide corridor from the J1 pad to U1, plus the pad cut-out): point-in-polygon check gives 0 In1/In2 polygons at P1, P2, P3 and under the trace; only B.Cu (z = 0) is solid there.
* All three rfsim ports use `ref_layer = In1.Cu` (the tool's default guess, not a layout error). A lumped port is a resistor from the pad down to the reference layer, so it ends in air and nothing flows: S22 ≈ +1, S11 ≈ open, S21 ≈ −80…−100 dB, independent of R/C values. This is also why refining the mesh made it "worse".
* Test H7: same model, only `ref_layer = B.Cu` for the 3 ports (coarse): S11 at 1 / 3 / 6 GHz changes from −0.35 / −1.7 / −5.8 dB to −2.1 / −7.1 / −10.8 dB and S21 at 1 GHz from −78 to −29 dB. Medium mesh (H9) gives the same (−2.1 dB / −26 dB at 1 GHz), so the rest is not a mesh effect: with 100 Ω ∥ 100 Ω to GND the board should still give S11 ≈ −10 dB and S21 ≈ −6…−10 dB at 0.2–1 GHz, but S11 stays ≈ −0.2…−2 dB. The shunt/return path (R1/R2 → F.Cu pour → vias → B.Cu, and ports 2/3 into the 1.44 mm tall reference) is still not right; this is **not yet solved**.
* Second error: the run used the rfsim dialog stackup (three equal 0.533 mm layers, In1 at 0.533 mm below F.Cu). The saved KiCad stackup is F.Cu / 0.1 mm prepreg (εr 4.4) / In1 / 1.24 mm core / In2 / 0.1 mm prepreg / B.Cu. With the real stackup and the old `runner.py` ports snap to zero length at medium mesh ("Lumped Element with zero (snapped) length is invalid! skipping: port_resist_1..3"), so use fine mesh or CPW-type ports.
* Consequence for the plan: "J1 line is ~90 Ω (ref In1 at 0.533 mm)" is wrong in its reason. The true reference is the coplanar F.Cu pour plus B.Cu 1.44 mm below; the grounded-CPW estimate is still ≈ 88 Ω for the 0.2032 mm trace, so the conclusion (≈ 90 Ω line) survives by coincidence. The circuit model below now uses h = 1.44 mm, εr 4.5.

Upstream search (2026-10-09): kicad-rfsim has no open issue on this (only #5, slash-rated values; PR #6 closed). The installed plugin (runner.py, 1175 lines) is much older than upstream main (v1.2.0, 2316 lines), where a lone R or C is no longer put on the series `LEtype=1` path ("R ≥ 150 Ω diverges to NaN" is documented in `_le_topology`) – this explains the NaN of test T3 but not the main fault: upstream runner on the same model (R on the classic path) still gives S11 −0.23 dB / S21 −49 dB at 1 GHz. openEMS issue #136 ("Weird Behavior of Lumped Components", closed, redirected to Discussions) describes the same symptom (lumped R has no effect, `AddMetal` does), no fix recorded.

### Second root cause (2026-10-09, evening): the copper was not in the simulation

A run in the KiCad 10 GUI with the saved stackup (F.Cu / 0.1 mm prepreg / In1 / 1.24 mm core / In2 / 0.1 mm prepreg / B.Cu, fine mesh) printed one `Warning: Unused primitive (type: LinPoly)` for **every** copper polygon (F.Cu 25, In1.Cu 7, In2.Cu 78, B.Cu 12 = all of them). openEMS does not treat a copper sheet as metal when no mesh line lies exactly on its z. `runner._mesh` merged close z lines (`_merge_close(zs, tol_z)`) with no anchors, so with a thin 0.1 mm dielectric the lines of the planes were averaged away: F.Cu 1.44 → 1.443, In1 1.34 → 1.343, In2 0.1 → 0.104, B.Cu 0.0 → −0.029 mm. The run had dielectric, lumped parts and ports, but **no copper**. The same effect explains the ports that were skipped at medium mesh ("zero (snapped) length").

Fix (`kicad-rfsim`, `plugins/runner.py`): the copper z values are anchors of the z merge (`RANK_FACE`). Regression test `validation/test_copper_lines.py` fails on the old runner (F.Cu 0.025 mm off the nearest line at coarse) and passes now.

Result with the fix (same model.json, port 1 only, 0.1–10 GHz, ports 2/3 = 50 Ω lumped on B.Cu, no "Unused primitive", converged after 7 740 steps = 0.7 ns instead of hundreds of thousands):

| f | S11 | Zin | S21 | S31 |
|---|---|---|---|---|
| 0.1 GHz | −3.1 dB | 9 + j2 Ω | −11.8 dB | −76 dB |
| 1 GHz | −3.7 dB | 12 + j16 Ω | −12.4 dB | −57 dB |
| 3 GHz | −6.6 dB | 34 + j41 Ω | −15.0 dB | −51 dB |
| 6 GHz | −11.2 dB | 74 + j25 Ω | −17.5 dB | −50 dB |
| 10 GHz | −16.1 dB | 66 + j8 Ω | −17.4 dB | −49 dB |

This is the first physically plausible EM result (passive, reciprocal-looking, fast convergence). It is not yet equal to the circuit model with 50 Ω pads (B0: ≈ −9.5 dB at low frequency, i.e. ≈ 25 Ω): the EM input resistance at 0.1 GHz is 9 Ω. Open: the value of the lumped R1/R2 over the box at this mesh, and the 1.44 mm tall lumped ports on 0.25 mm pads. The earlier statements above about "ports over a void" and "CPW not converging" were made on runs that had **no copper** and should be repeated with the fixed runner.

### CPW port at J1 (test, 2026-10-09, not conclusive)

Port 1 as Coplanar (CPW) (gap 0.493 mm measured at the pad), ports 2/3 lumped on B.Cu, only R1/R2/C1/C2 modelled, coarse mesh, generic stackup, 0.3–6 GHz, domain 2 mm margin (1.3 M cells, timestep 30 fs).

* The runs are slow: the CPW gap needs 4 cells per gap, so ≈ 40 steps/s; the energy decays only to ≈ −11 … −13 dB in 60 000 steps (1.8 ns) and the −30 dB end criterion is not reached (excitation alone is 33 000 steps; a converged run needs more than ≈ 150 000 steps, over 1 h).
* With the package parasitics of C1/C2 (R+L+C on the series `LEtype=1` path) the energy **grows** exponentially after ≈ 45 000 steps (instability, the "slow mode" that upstream `openems_le_growth.py` studies). With `parasitics=false` (ideal C) the run is stable.
* Truncated at 60 000 steps neither variant is usable: CPW port S11 −0.2 … −1 dB (Zin ≈ 300–3000 Ω reactive), and the lumped-port twin even gives |S11| > 1 (non-passive). A truncated FDTD run says nothing about S11.
* So the CPW port itself is accepted by the solver, but a convergence-quality result needs a long run (or a smaller domain / larger end criterion) that was not completed.

## 3. Result 2 — circuit-level model of the same board (cross-check, case B)

Sections read from the rfsim layout (mm): pad 0.8 wide × 1.1 (to pad edge), 0.2032 × 0.65 → R1 tap → 0.30 × 1.0 → R2 tap → 0.2032 × 1.1 → C1 → 0.1524 × 1.68 → U1:1.
INLO: 0.25 × 1.7 → C2 → GND. Lines = grounded CPW (Simons), 0.20 mm gap, ground plane B.Cu 1.44 mm below (In1/In2 void), εr 4.5: Z0 = 88 Ω (0.2032), 95 Ω (0.1524), 71 Ω (pad). R1, R2 = 100 Ω + 0.25 nH ESL, C1/C2 = 47 nF + 0.25 nH + 35 mΩ.
Not modelled: connector body/launch, vias, pad-to-ground fringe C, the step discontinuities.

| Band | worst \|S11\| (B, current layout) | B2 (50 Ω traces, 1.21 mm wide) | B_chart (chip data from Fig. 15 at ≥ 5.8 GHz) |
|---|---|---|---|
| 1–100 MHz | −26.5 dB | −26.4 dB | −26.5 dB |
| 0.1–1 GHz | −25.4 dB | −20.6 dB | −25.4 dB |
| 1–3 GHz | −11.2 dB @ 2.95 GHz | −9.5 dB | −11.2 dB |
| 3–6 GHz | **−8.7 dB @ 3.6 GHz** | −7.8 dB | **−4.6 dB @ 4.7 GHz** |
| 6–10 GHz | −9.2 dB @ 6.3 GHz | −9.2 dB | −7.6 dB @ 6.0 GHz |

\|S11\| < −10 dB on 93 % of the log sweep (B) vs 91 % (B2). **Above ~4 GHz the result depends on which chip data is right**: the datasheet table (110 Ω ∥ 0.05 pF at 5.8 GHz) and the Figure-15 chart (≈ 210 Ω ∥ 0.5 pF there) disagree, and B_chart is 4 dB worse at 4–5 GHz. Only a VNA measurement of the real input settles it. Above 8 GHz the model now uses the chart point at 10 GHz instead of holding the 8 GHz value. Low-frequency value is set by R1‖R2 = 50 Ω in parallel with 500 Ω.
The 50 Ω shunt is what gives the match; the trace itself is secondary. The chip's capacitive input is partly compensated by the ~90 Ω (inductive) lines, so
**re-drawing the line as 50 Ω (plan item 1) makes the 3–4 GHz dip ≈ 1 dB worse in this model**, not better.

## 4. Verdict

* Is S11 optimal? **Not with certainty — the EM model as run cannot show it (ports floated over a void In1); the circuit model says "good, not optimal"**: ≤ −10 dB almost everywhere, weak regions 3–4 GHz (−8.7 dB) and ~6 GHz (−9.2 dB), set mainly by the chip's 300 Ω ‖ 0.33 pF at 3.6 GHz and the 50 Ω shunt.
* Cheap levers to test in Qucs (case B) before touching layout: R1‖R2 value, small series L/C tuning at INHI, shunt position; not trace width.
* Next steps: (1) rerun rfsim with ports referenced to B.Cu (or CPW ports) on fine mesh, then terminate with the `.s2p` series load; (2) re-evaluate plan items in `RF_Input_Optimization_Plan.md`; (3) measure real S11 with the VNA at J1 and compare with case B.

## 5. Files (`sim/qucs/`)

| File | Content |
|---|---|
| `ad8317_input_model.py` | AD8317 Zin model + Touchstone writers |
| `AD8317_INHI_1MHz_10GHz[_hold|_chart].s1p`, `AD8317_INHI_INLO_series_1MHz_10GHz[_hold|_chart].s2p` | chip S-parameters (1-port, and series 2-port for P2–P3), three variants |
| `rfsim_J1_U1_3port_1MHz_10GHz.s3p` | rfsim result, Qucs-readable (wrapped rows), flat-extended to 1 MHz |
| `case_A0/A1/B/B_chart/B0/B2_*.net` | qucsator netlists (`.dat` results are git-ignored); run: `qucsator -i case_A1_rfsim_ad8317.net -o out.dat` from this folder |
| `run_s11_study.py` | regenerates everything (`--rfsim <results.s3p>` rebuilds the 3-port; `QUCSATOR` env var or PATH finds qucsator; `STACKUP=dialog` reproduces the generic-stackup variant), also cross-checks A1 with an independent numpy Y-matrix reduction (max difference 4e-13) |
| `s11_results.csv`, `s11_1MHz_10GHz.png` | S11 [dB] per case, plot |

The Qucs GUI could not be driven headless here, so no `.sch` was produced; the `.net` files are the simulation inputs.
