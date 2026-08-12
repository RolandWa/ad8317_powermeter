# hfss_sma_coupon_gcpw_opt.py — Design Document

## Purpose

`hfss_sma_coupon_gcpw_opt.py` is a fully parametric HFSS IronPython script for
**iterative optimization of a single SMA-to-GCPW transition**.  It uses a short
(20 mm default) **half-model** with one SMA connector on the left and a radiation
boundary on the right face that absorbs the propagating GCPW mode.

Wave port **WP1** is placed on the circular outer face of the SMA connector
(coaxial cable end), found automatically via `GetModelBoundingBox` after the
3D component is inserted.  The SMA component's internal port P1 (at the
PCB-pin interface) is preserved and not used as the excitation — WP1 and P1
are at different X positions and do not conflict.  Each Optimetrics iteration
solves one excitation, cutting solve time roughly in half compared to the full
two-connector model.

Eight geometry parameters are registered as **HFSS design variables** so
Optimetrics can sweep them without re-running the script.  Two Optimetrics
setups are created automatically:

- **ParametricSetup1** — coarse 1-D sensitivity scan (4 LC vars × 6 pts = 24 solves).
  Run first to identify which LC parameters matter most.
- **OptimizationSetup1** — **Screening (Search-based)** / `DX SCREENING`, goal
  S(WP1,WP1) ≤ −23 dB over 0.01–20 GHz evaluated on `RF_Sweep`.

Run from inside AEDT 2026.1: **Tools → Run Script → select this file.**
No external packages required.  Edit the `CONFIG` section before running.

---

## Differences from the Base GCPW Script

| Feature | `hfss_sma_coupon_gcpw.py` | `hfss_sma_coupon_gcpw_opt.py` |
|---|---|---|
| Board length | 60 mm, two SMA connectors | **20 mm**, one SMA connector |
| Right termination | SMA connector + wave port WP2 | **Radiation boundary** (absorbs GCPW mode) |
| Ports | WP1 (left SMA), WP2 (right SMA) | **WP1 only** (circular, outer SMA face) |
| F.Cu signal conductor | 1 uniform-width box | **3 boxes** — entrance + stub + center-to-right-edge |
| Inner-layer planes | Solid full-width copper | In1.Cu and In2.Cu have **parametric voids** at left end |
| HFSS design variables | None | **8 parametric variables** (see table below) |
| Optimetrics setup | None | **ParametricSetup1** (scan) + **OptimizationSetup1** (DX SCREENING) |
| Solve time per iteration | ~full (two excitations) | **~half** (one port, shorter domain) |
| Design name | `SMA_Coupon_GCPW_4L` | `SMA_Coupon_GCPW_Opt_4L` |

---

## Coordinate System

```
Z
│  F.Cu (top)
│  ─ ─ ─
│  Pre1 dielectric
│  ─ ─ ─
│  In1.Cu   ← void at left end
│  ─ ─ ─
│  Core1
│  ─ ─ ─
│  In2.Cu   ← void at left end
│  ─ ─ ─
│  Pre2 dielectric
│  ─ ─ ─
│  B.Cu (bottom)
└────────────────── X (board long axis)
```

- **Origin**: centre of the board in XY; z = 0 at the bottom of B.Cu.
- **X**: board long axis; board spans `[−hx, +hx]` where `hx = BOARD_LENGTH/2 = 10 mm`.
- **Left end** (x = −hx): SMA connector pin contact; `P1` (component internal, kept).
  Wave port `WP1` is at the outer coaxial cable face, x < −hx.
- **Right end** (x = +hx): Radiation boundary `Rad_PML_Right` (no physical connector).
- Inner-layer voids only at the **left** board end.

---

## HFSS Design Variables

All eight variables are registered in AEDT before geometry is built.
Optimetrics updates them without re-running the script.  To adjust initial
values, change the Python constants in the `OPTIMIZATION VARIABLES` section.

| AEDT variable | Python CONFIG | Default | Units | Constraint | Effect |
|---|---|---|---|---|---|
| `comp_induct_w` | `COMP_INDUCT_W` | 0.25 | mm | < `TRACE_WIDTH` | Less inductance as value increases |
| `comp_induct_len` | `COMP_INDUCT_LEN` | 0.40 | mm | < `hx` | More series inductance as value increases |
| `comp_cap_w` | `COMP_CAP_W` | 0.65 | mm | < 2×(htw+gap) | More shunt capacitance as value increases |
| `comp_cap_len` | `COMP_CAP_LEN` | 0.25 | mm | < `hx − comp_induct_len` | More shunt capacitance |
| `void_l2_x` | `VOID_L2_X` | 4.00 | mm | ≤ `hx` | Larger void on In1.Cu |
| `void_l2_y` | `VOID_L2_Y` | 2.50 | mm | ≤ `hy` | Wider void on In1.Cu |
| `void_l3_x` | `VOID_L3_X` | 3.00 | mm | ≤ `hx` | Larger void on In2.Cu |
| `void_l3_y` | `VOID_L3_Y` | 2.00 | mm | ≤ `hy` | Wider void on In2.Cu |

**`comp_cap_w` constraint:** `comp_cap_w / 2` must remain less than
`(TRACE_WIDTH/2 + GAP_MM)` so the stub does not touch the coplanar GND strips.
For default stackup (TRACE_WIDTH=0.35, GAP_MM=0.20): max = 0.70 mm.

**Optimetrics bounds** (set in `create_optimetrics()` and `create_parametric_sweep()`):

| Variable | Min | Max | Physical reason |
|---|---|---|---|
| `comp_induct_w` | 0.05 mm | TRACE_WIDTH − 0.05 = **0.30 mm** | Must be narrower than main trace |
| `comp_induct_len` | 0.10 mm | **1.50 mm** | Beyond 1.5 mm the Z-mismatch dominates |
| `comp_cap_w` | TRACE_WIDTH + 0.01 = **0.36 mm** | TRACE_WIDTH + 2×GAP − 0.05 = **0.70 mm** | Must exceed trace W to add shunt C |
| `comp_cap_len` | 0.10 mm | **1.50 mm** | Same ceiling as inductive section |
| `void_l2_x` | 0.50 mm | **6.00 mm** | In1.Cu (nearest inner); leave ≥4 mm solid |
| `void_l2_y` | 0.50 mm | **3.50 mm** | Leave ≥1.5 mm of solid GND at edges |
| `void_l3_x` | 0.50 mm | **5.00 mm** | In2.Cu farther from connector — less effect |
| `void_l3_y` | 0.50 mm | **3.00 mm** | Less lateral extent needed for In2.Cu |

---

## F.Cu Signal Conductor Geometry

### Half-model: 3 segments only (left-to-right)

```
     -hx                                                        +hx
      │                                                          │
──────┼──────────────────────────────────────────────────────────┼──────  ← +hy
      │         CGND_FCu_PosY (full length, fixed)               │
──────┼──────────────────────────────────────────────────────────┼──────  ← +htw+gap
      │  ←ent→  ←─cap─→  ←────────── Trace_FCu_Ctr ───────────→ │
      │   ╔════╗ ╔══════╗ ╔═════════════════════════════════════╗│
      │   ║Ent ║ ║ Cap  ║ ║   50-ohm main trace                 ║│  ← +htw
─ Y=0─┼───╠════╬═╬══════╬═╬═════════════════════════════════════╬┼─────
      │   ║Ent ║ ║ Cap  ║ ║   50-ohm main trace                 ║│  ← -htw
      │   ╚════╝ ╚══════╝ ╚═════════════════════════════════════╝│
──────┼──────────────────────────────────────────────────────────┼──────  ← -htw-gap
      │         CGND_FCu_NegY (full length, fixed)               │
──────┼──────────────────────────────────────────────────────────┼──────  ← -hy
      ↑ SMA + P1                             Radiation boundary ↑
```

### Named objects

| Object | X start (AEDT expr) | X size (AEDT expr) | Y centre | Y size | Material |
|---|---|---|---|---|---|
| `Trace_FCu_L_Ent` | `-10mm` | `comp_induct_len` | 0 | `comp_induct_w` | copper |
| `Trace_FCu_L_Cap` | `-10mm+comp_induct_len` | `comp_cap_len` | 0 | `comp_cap_w` | copper |
| `Trace_FCu_Ctr` | `-10mm+comp_induct_len+comp_cap_len` | `20mm−comp_induct_len−comp_cap_len` | 0 | `TRACE_WIDTH` | copper |

*(hx = 10 mm for default 20 mm board)*

The Ctr segment extends all the way to x = +hx (right PCB edge / radiation
boundary face).  There are **no right-side compensation pieces** because there
is no connector on the right.

### LC network interpretation

```
SMA pin ── [L series: narrow entrance] ── [C shunt: wide stub] ── 50-ohm GCPW ──► PML
```

---

## Inner-Layer Ground Voids

Voids on In1.Cu and In2.Cu reduce parasitic capacitance between the SMA
connector body and the inner ground planes.

### Void geometry (left/SMA end only)

```
Layer In1.Cu:      ← void_l2_x →
   ┌──────┬─────────────┬──────────────────────────────────────┐
   │      │   void      │          solid copper                │  +void_l2_y
   │      │  (vacuum)   │                                      │
   │      │             │                              PML → │  │
   │      │             │          solid copper                │  -void_l2_y
   └──────┴─────────────┴──────────────────────────────────────┘
   x=-hx              x=-hx+void_l2_x                     x=+hx

Layer In2.Cu: same pattern with void_l3_x / void_l3_y
```

### AEDT expression geometry for voids

| Parameter | AEDT expression |
|---|---|
| X start | `"-10.000000mm"` (fixed) |
| X size | `"void_l2_x"` (variable) |
| Y start | `"-void_l2_y"` (variable, AEDT unary minus) |
| Y size | `"2*void_l2_y"` (variable times 2) |

---

## Radiation Boundary (Right End)

`Rad_PML_Right` is assigned to a rectangle at x = +hx spanning the full board
cross-section (Y: −hy to +hy, Z: B.Cu bottom to F.Cu top).

The radiation boundary (absorbing boundary condition) introduces a residual
GCPW reflection of approximately −20 to −30 dB — acceptable because the
connector transition under optimization dominates S11 by 10–20 dB.

**Why not a second wave port?**
A wave port at the right would be a perfect match for the GCPW mode but adds
one full port excitation per Optimetrics iteration.  With a radiation boundary,
only P1 is excited, halving solve time per iteration.

---

## Optimetrics Setup

Two setups are created automatically by the script.

### ParametricSetup1 — sensitivity scan (run first)

| Parameter | Value |
|---|---|
| Type | Parametric (independent 1-D sweep) |
| Variables swept | 4 LC params: `comp_induct_w/len`, `comp_cap_w/len` |
| Points per variable | 6 (linear spacing over physical range) |
| Total solves | 24 adaptive solutions |
| Goals | None — inspect S(WP1,WP1) plots per variable |

**Purpose:** confirm sensitivity of S11 to each LC parameter before
committing CPU time to the space-sampling optimiser.

> **Void variables** (`void_l2_x/y`, `void_l3_x/y`) are registered as AEDT
> design variables (geometry uses them) but are **not swept or optimised**.
> Set them manually in the AEDT Variables tab before running Optimetrics if
> you want to explore a different void geometry.

### OptimizationSetup1 — adaptive surrogate optimiser (run after scan)

| Parameter | Value |
|---|---|
| Optimizer | **Adaptive Single-Objective(Gradient)**  —  API string `"kDX ASO"` (confirmed by recorder) |
| Phase 1: initial samples | **19**  (DOE space-filling, from `InitSamples` in DXOptimizerOptionData) |
| Phase 2: max total evaluations | **68**  (`MaxEvaluations` — AEDT default with "Use Default Setting" checked) |
| Convergence tolerance | 0.001  (`ConvergenceTolerance` in DXOptimizerOptionData) |
| Min evaluations | 10  (`MinNumIteration` in AnalysisStopOptions) |
| Cost function norm | L2 |
| Metric | dB(S(WP1,WP1)) |
| Condition | ≤ −23 dB |
| Frequency range | 0.01 GHz – 20 GHz (all RF_Sweep points) |
| Solution | `HFSS_Adaptive : RF_Sweep` |
| Optimised variables | `comp_induct_w`, `comp_induct_len`, `comp_cap_w`, `comp_cap_len` |
| Fixed variables | `void_l2_x/y`, `void_l3_x/y` (held at CONFIG defaults) |

**Why 19 and 68?**  Values confirmed by the AEDT 2026.1 script recorder (2026-08-12)
for n=4 variables with "Use Default Setting" checked.  The Optimizer Options dialog
shows "Number of Initial Samples: 17" as a display hint, but the underlying API
parameter `InitSamples` is **19** (the DOE-build phase).  `MaxEvaluations=68` is
the total HFSS solve budget (including both DOE and refinement phases).
Compare to DX Screening which uses 100 initial samples regardless of variable count
— 81 fewer HFSS solves for this 4-variable problem.

To launch optimisation: **Optimetrics → OptimizationSetup1 → Analyze**.

After convergence, inspect S(WP1,WP1) in the broadband RF_Sweep plot.

Variables and their starting points are read from the AEDT design variable
table (initially set to the Python CONFIG defaults).

---

### How the Script Creates OptimizationSetup1

The AEDT 2026.1 IronPython API requires a **three-step sequence** — discovered
by recording a manual setup from the AEDT GUI (2026-08-12):

```python
# Step 1 — create the setup skeleton with an empty Variables block
# Optimizer Options (from GUI dialog, "Use Default Setting" checked, n=4 vars):
#   Number of Initial Samples    = 17  (= 4n+1)
#   Maximum Number of Evaluations= 68  (= 4*(4n+1))
#   Convergence Tolerance        = 0.001
# These map to AnalysisStopOptions: MaxNumIteration=68, MinNumIteration=17,
# RelGradientTolerance=0.001.  No separate optimizer option data block needed
# (Adaptive Single-Objective uses AnalysisStopOptions directly, unlike DX
# SCREENING which had its own "NAME:DXOptimizerOptionData" sub-block).
oOpt.InsertSetup("OptiOptimization",
    ["NAME:OptimizationSetup1",
     "IsEnabled:=", True,
     ["NAME:ProdOptiSetupDataV2", "SaveFields:=", False, ...],
     ["NAME:StartingPoint", "comp_cap_len:=", "0.25mm", ...],
     "Optimizer:=", "kDX ASO",      # confirmed by AEDT recorder 2026-08-12
     ["NAME:AnalysisStopOptions",
      "StopForNumIteration:=", True,  "StopForElapsTime:=", False,
      "StopForSlowImprovement:=", False,  "StopForGrdTolerance:=", False,
      "MaxNumIteration:=", 68,      # matches MaxEvaluations
      "MaxSolTimeInSec:=", 3600,
      "RelGradientTolerance:=", 0,  # 0 = use MaxNumIteration as primary stop
      "MinNumIteration:=", 10],
     "CostFuncNormType:=", "L2",
     "PriorPSetup:=", "", "PreSolvePSetup:=", True,
     ["NAME:Variables"],            # <-- empty; bounds come in Step 3
     ["NAME:LCS"],
     ["NAME:Goals",
      ["NAME:Goal",
       "ReportType:=", "Modal Solution Data",
       "Solution:=",   "HFSS_Adaptive : RF_Sweep",
       ["NAME:SimValueContext", "Domain:=", "Sweep"],
       "Calculation:=", "dB(S(WP1,WP1))",
       "Name:=",        "dB(S(WP1,WP1))",
       ["NAME:Ranges",
        "Range:=", ["Var:=", "Freq", "Type:=", "rd",
                    "Start:=", "0.01GHz", "Stop:=", "20GHz",
                    "DiscreteValues:=", "0.01GHz,0.059975GHz,...,20GHz"]],
                    # ^ all 401 RF_Sweep points, generated in script from
                    # F_START/F_STOP/F_POINTS; confirmed by AEDT recorder
       "Condition:=", "<=",
       ["NAME:GoalValue", "GoalValueType:=", "Independent",
        "Format:=", "Real/Imag", "bG:=", ["v:=", "[-23;]"]],
       "Weight:=", "[1;]"]],
     "Acceptable_Cost:=", 0, "Noise:=", 0.0001,
     "UpdateDesign:=", False, "UpdateIteration:=", 5,
     "KeepReportAxis:=", True, "UpdateDesignWhenDone:=", True,
     ["NAME:DXOptimizerOptionData",     # shared block — used by both kDX ASO
      "InitSamples:=", 19,              # and DX SCREENING (different values)
      "MaxEvaluations:=", 68,
      "ConvergenceTolerance:=", 0.001,
      "RandomSeed:=", 0, "MaxCycles:=", 10, "ScreenSamples:=", 400,
      "StartingPoints:=", 12, "MaxDomainReductions:=", 20,
      "PercentDomainReductions:=", 0.1, "RetainedDomainPerIteration:=", 40]])

# Step 2 — mark the 4 LC design variables as Included in this optimization
oDesign.ChangeProperty(
    ["NAME:AllTabs",
     ["NAME:LocalVariableTab",
      ["NAME:PropServers", "LocalVariables"],
      ["NAME:ChangedProps",
       ["NAME:comp_induct_w",   ["NAME:Optimization", "Included:=", True]],
       ["NAME:comp_induct_len", ["NAME:Optimization", "Included:=", True]],
       ["NAME:comp_cap_w",      ["NAME:Optimization", "Included:=", True]],
       ["NAME:comp_cap_len",    ["NAME:Optimization", "Included:=", True]]]]])

# Step 3 — edit the setup to add variable bounds (alphabetical order)
# Variable format: "varname:=", ["i:=", True, "int:=", False,
#                  "Min:=", "0.050mm", "Max:=", "0.300mm",
#                  "MinStep:=", ..., "MaxStep:=", ...,
#                  "MinFocus:=", ..., "MaxFocus:=", ...,
#                  "UseManufacturableValues:=", "false",
#                  "Level:=", "[0.050000: 0.300000] mm"]
oOpt.EditSetup("OptimizationSetup1", [...same body with full Variables...])
```

**Why three steps?**  If variable bounds are included in `InsertSetup` before
`ChangeProperty` marks them as `Included`, AEDT silently ignores the bounds.
The `ChangeProperty` call must come first.  This three-step pattern was
confirmed by the AEDT script recorder.

---

### Available Optimization Methods in AEDT 2026.1

Full list visible in **Setup Optimization → Optimizer** dropdown.
API strings marked † are confirmed by AEDT script recording; others are best-guess
— if InsertSetup fails with "optimizer not found", record a manual setup from the
GUI to retrieve the exact API string.

| GUI label | API string | Type | HFSS solves (est.) | Notes |
|---|---|---|---|---|
| **Adaptive Single-Objective(Gradient)** | `"kDX ASO"` † | Surrogate+gradient | **19–68** (InitSamples=19, MaxEval=68) | **Best for this problem.  Used by this script.** |
| Screening(Search-based) | `"DX SCREENING"` † | DOE surrogate | 100+ init | Confirmed by recording. Wasteful for 4 known-smooth vars. |
| Nonlinear Programming by Quadratic Lagrangian(Gradient) | `"NLPQL"` | Gradient | ~30–60 | Pure gradient; needs good start |
| Adaptive Multiple-Objective(Random Search) | `"Adaptive Multi-Objective"` | Surrogate | — | Multi-goal only — not applicable here |
| Multi-Objective Genetic Algorithm(Random-search) | `"Multi-Objective Genetic Algorithm"` | Evolutionary | 500+ | Multi-goal; very expensive for HFSS |
| Mixed-Integer Sequential Quadratic Programming(Gradient and Discrete) | `"MISQP"` | Gradient+discrete | — | For integer variables; overkill here |
| MATLAB | `"MATLAB"` | External | — | Requires MATLAB licence |
| *(Legacy)* Quasi Newton(Gradient) | `"Quasi Newton"` | Gradient | ~50–75 | Fast after a good start; use after Adaptive Single-Obj converges |
| *(Legacy)* Sequential Nonlinear Programming(Gradient) | `"Sequential Nonlinear Programming"` | Gradient | ~50–100 | Older NLPQL variant |
| *(Legacy)* Sequential Mixed Integer NonLinear Programming(Gradient and Discrete) | `"Sequential Mixed Integer NonLinear Programming"` | Gradient+discrete | — | Integer vars |
| *(Legacy)* Pattern Search(Search-based) | `"Pattern Search"` | Derivative-free | ~100–200 | Robust but slow |
| *(Legacy)* Genetic Algorithm(Random search) | `"Genetic Algorithm"` | Evolutionary | 500+ | Not suitable for expensive HFSS solves |

**Optimizer Options dialog** (click **Setup...** next to the dropdown):

When **Use Default Setting** is checked, AEDT derives the values from n = number
of optimised variables.  For Adaptive Single-Objective with n=4:

| Dialog field | API parameter | Value (n=4) |
|---|---|---|
| Number of Initial Samples (display only) | `DXOptimizerOptionData / InitSamples` | **19** |
| Maximum Number of Evaluations | `DXOptimizerOptionData / MaxEvaluations` | **68** |
| Convergence Tolerance | `DXOptimizerOptionData / ConvergenceTolerance` | **0.001** |
| — | `DXOptimizerOptionData / MaxCycles` | 10 |
| — | `DXOptimizerOptionData / ScreenSamples` | 400 |
| — | `DXOptimizerOptionData / StartingPoints` | 12 |
| — | `DXOptimizerOptionData / MaxDomainReductions` | 20 |
| — | `DXOptimizerOptionData / PercentDomainReductions` | 0.1 |
| — | `DXOptimizerOptionData / RetainedDomainPerIteration` | 40 |
| — | `AnalysisStopOptions / MaxNumIteration` | 68 |
| — | `AnalysisStopOptions / MinNumIteration` | 10 |
| — | `AnalysisStopOptions / RelGradientTolerance` | 0 |

**Recommended workflow for this problem:**
1. Run **ParametricSetup1** (24 solves) to see which LC params have the most
   sensitivity and get a feel for the landscape.
2. Run **OptimizationSetup1** (Adaptive Single-Objective, up to 68 solves).
   Phase 1 builds a 17-point surrogate; Phase 2 refines toward the goal.
3. If the result is close but not quite at −23 dB, note the best variable
   values, update the `StartingPoint` in the script, switch the optimizer in
   the AEDT GUI to **Quasi Newton**, and re-run for fast gradient convergence.

---

## Mesh Strategy

```
Mesh_Transition   → MeshZone_Trans_Left only                    [MESH_TRANS_MM]
Mesh_Trace        → Trace_FCu_Ctr                               [MESH_TRACE_MM]
Mesh_Trace_Comp   → Trace_FCu_L_Ent, Trace_FCu_L_Cap           [MESH_TRACE_MM]
Mesh_CoplnarGND   → CGND_FCu_PosY, CGND_FCu_NegY               [MESH_CGND_MM]
Mesh_Planes       → inner + B.Cu planes                         [MESH_PLANE_MM]
Mesh_Vias_xx      → via barrels                                 [MESH_VIA_MM]
Mesh_Skin_xx      → all copper (skin depth)                     [MESH_SKIN_MM]
```

`MeshZone_Trans_Right` is created by `make_transition_zones` and then immediately
deleted in `main()`.  It is unnecessary because there is no SMA connector at
the right end, and it would extend 3 mm past the PML face.

---

## Manual Tuning Workflow

Use this before running Optimetrics to get a feel for the sensitivities:

**Primary metric:** `S(WP1,WP1)` = connector return loss (WP1 = circular port at outer SMA face).

1. **Baseline**: set all variables to zero-effect values (entrance = trace width,
   stub = trace width, voids near zero).  Record uncompensated S11.

2. **Add voids first**: increase `void_l2_x`, `void_l2_y`, `void_l3_x`, `void_l3_y`.
   Expected: resonance above 10 GHz reduces.

3. **Add inductance**: decrease `comp_induct_w` and/or increase `comp_induct_len`.
   Watch 1–5 GHz S11.  Too much → rising low-frequency S11.

4. **Add capacitance**: increase `comp_cap_w` and/or `comp_cap_len`.
   Balances inductance; target flat S11 < −20 dB.

5. **Fine-tune**: iterate inductance ↔ capacitance with voids fixed.

6. **Run Optimetrics**: once in the right ballpark, launch **OptimizationSetup1**
   (DX SCREENING samples 100 initial points across the full variable space, then
   refines toward the best region).  After it converges, re-run RF_Sweep to
   verify broadband S11.  If you already have a good starting point from a
   previous screen, switch the optimizer in the AEDT GUI to **Quasi Newton** for
   faster gradient-based convergence.

7. **Transfer to full model**: copy final variable values into
   `hfss_sma_coupon_gcpw.py` and verify with the 60 mm two-connector model.

---

## Known AEDT 2026.1 IronPython Constraints

- `Insert3DComponent` ignores `TargetCS` rotation axes (translation only).
  Right connector corrected via `oEditor.Mirror` about plane x = hx.

- Mesh refinement boxes must have `"Flags:=", "NonModel#"` to avoid "Parts
  intersect" solver errors.

- `UseIntLine=False` is required for wave ports in AEDT 2026.1.

- `InsertSetup("OptiOptimization", ...)` requires the **exact AEDT 2026.1
  goal structure** discovered by recording a manual setup.  Wrong patterns
  that cause *"There is no goal defined"*:
  - Optimizer string `"Quasi Newton"` — use `"DX SCREENING"` (even if you later
    switch in the GUI; the API only accepts the internal string).
  - Stop criteria key `"NAME:AnalysisStopCriteriaData"` — must be
    `"NAME:AnalysisStopOptions"` with keys `StopForElapsTime`, `MaxSolTimeInSec`,
    `RelGradientTolerance`, `MinNumIteration`.
  - Goal block `"NAME:GoalFunction"` with `"NAME:SimValueExpression"` /
    `"NAME:GoalValueSingle"` — must be `"NAME:Goals"` / `"Calculation:="` /
    `["NAME:SimValueContext", "Domain:=", "Sweep"]` / `["NAME:GoalValue",
    "GoalValueType:=", "Independent", "bG:=", ["v:=", "[-23;]"]]`.
  - Variable format `["NAME:varname", "Min:=", ...]` — must be the dict-style
    `"varname:=", ["i:=", True, "int:=", False, "Min:=", ..., "Level:=", ...]`.
  - Solution `"HFSS_Adaptive : LastAdaptive"` — must be `"HFSS_Adaptive : RF_Sweep"`.
  - Variables in `InsertSetup` — must be an **empty** `["NAME:Variables"]`.
    Add bounds only in the `EditSetup` call (Step 3) after `ChangeProperty`
    marks variables as `Included:=True` (Step 2).

- Inner-layer void boxes are normal model solids subtracted before solve.
  After subtract, tool bodies are consumed (`KeepOriginals=False`).

- SM-2400071 orientation: the component places correctly with a CS whose X-axis
  points in the −X direction (`xvec=(−1,0,0)`).  An earlier script revision
  produced a mis-oriented connector body (`2023R1_HRMG_300_468B1`) that required
  two manual rotations (90° Y, then 90° X around the global origin) after
  running the script.  The current `place_sma` CS setup avoids this.

- AEDT expression strings passed as box size/position parameters
  (e.g. `"comp_induct_len"`, `"-10.000000mm+comp_induct_len"`) are evaluated
  live by AEDT when Optimetrics updates variable values — geometry updates
  without re-running the script.

---

## Files

| File | Role |
|---|---|
| `sim/hfss/hfss_sma_coupon_gcpw_opt.py` | This script |
| `sim/hfss/hfss_sma_coupon_gcpw.py` | Base GCPW script (two connectors, no Optimetrics) |
| `sim/hfss/SM-2400071.a3dcomp` | SMA 3D component (must be in same folder) |

---

## References

- toammann/Multilayer_SMA2Microstrip: Rosenberger 32K242-40ML5 on AISLER 6LayerHD.
  Compensation reference values: `l01_compL_y=0.25`, `l01_compL_x=0.40`,
  `l01_compC_y=1.20`, `l01_compC_x=0.25`, `l03_gnd_ref_cutout=2mm`.
- JLCPCB 4-layer stackup: B.Cu / Pre2(0.2101) / In2.Cu / Core1(1.0650) / In1.Cu / Pre1(0.2101) / F.Cu
- See `memory/hfss_pyaedt_board.md` for full IronPython API reference.
