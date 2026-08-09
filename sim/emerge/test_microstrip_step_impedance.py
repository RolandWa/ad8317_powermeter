"""
Microstrip step-impedance test — two 50 Ω → 100 Ω → 50 Ω transitions.

Geometry (all mm):
  Board   : 20 x 5 mm, 1.6 mm thick FR4 (er=4.4, tand=0.02)
  Section A: 50 Ω  trace (W=3.0 mm)  x = 0  → 7  mm
  Section B: 100 Ω trace (W=0.65 mm) x = 7  → 13 mm  (6 mm long)
  Section C: 50 Ω  trace (W=3.0 mm)  x = 13 → 20 mm
  Port 1  : x=2 mm  (50 Ω reference)
  Port 2  : x=18 mm (50 Ω reference)

Physics:
  The impedance discontinuities at x=7 mm and x=13 mm create reflections Γ = (Z1-Z2)/(Z1+Z2).
  50→100 Ω: Γ = +0.333 (+9.5 dB positive reflection)
  100→50 Ω: Γ = -0.333
  Multiple reflections create S11/S21 ripple with periodicity Δf = v_p/(2·L_step).

Run:
  python sim/emerge/test_microstrip_step_impedance.py
  python sim/emerge/test_microstrip_step_impedance.py --plots
  python sim/emerge/test_microstrip_step_impedance.py --solver gpu

Output: sim/emerge/results/microstrip_step/
"""

import sys, time, pathlib, threading, math
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── CLI ───────────────────────────────────────────────────────────────────────
_PLOTS_ENABLED = "--plots"         in sys.argv
_SHOW_GEOMETRY = "--show-geometry" in sys.argv
_SHOW_MESH     = "--show-mesh"     in sys.argv
_WIDE          = "--wide"          in sys.argv   # use 6 mm (≈32 Ω) instead of 0.65 mm (≈96 Ω)
_SOLVER        = "auto"
for _i, _a in enumerate(sys.argv[1:], 1):
    if _a == "--solver" and _i < len(sys.argv):
        _SOLVER = sys.argv[_i + 1].lower()

_OUTDIR = pathlib.Path(__file__).parent / "results" / "microstrip_step"
_OUTDIR.mkdir(parents=True, exist_ok=True)

def log(msg): print(msg, flush=True)

# ── EMerge imports ────────────────────────────────────────────────────────────
try:
    import emerge
    from emerge import Simulation
    from emerge._emerge.geo.pcb         import PCBNew, PCBLayer
    from emerge._emerge.geo.open_region import open_pml_region
    from emerge._emerge.physics.microwave.bcs.boundary_conditions import AbsorbingBoundary
    from emerge._emerge.physics.microwave.touchstone import generate_touchstone
    from emerge._emerge.solver import (
        SolverCuDSS, SolverPardiso, SolverSuperLU,
        _CUDSS_AVAILABLE, _PARDISO_AVAILABLE,
    )
    from emerge import Material
    try:
        from gerber_builder import gmsh_view_geometry, gmsh_view_mesh
    except Exception:
        gmsh_view_geometry = gmsh_view_mesh = None
    log(f"EMerge {emerge.__version__} OK")
except ImportError as e:
    log(f"ERROR: {e}"); sys.exit(1)

# ── Board geometry (metres) ───────────────────────────────────────────────────
BW   = 20e-3   # board X (20 mm — long enough for impedance mismatch ripple)
BH   =  5e-3   # board Y
BT   =  1.6e-3 # FR4 thickness
TT   = 35e-6   # copper thickness
ER   = 4.4
TAND = 0.02

# ── Trace sections ────────────────────────────────────────────────────────────
TW_50  = 3.0e-3    # 50 Ω trace width  (W/h=1.875 → Z0≈50 Ω)
# Section B: narrow = 0.65 mm (≈96 Ω, Gamma>0);  wide = 6 mm (≈32 Ω, Gamma<0)
TW_B   = 6.0e-3 if _WIDE else 0.65e-3

X_STEP1 =  7e-3   # 50 Ω → section B transition
X_STEP2 = 13e-3   # section B → 50 Ω transition
L_STEP  = X_STEP2 - X_STEP1   # length of 100 Ω section

PX1    = 2e-3    # Port 1 x-position
PX2    = 18e-3   # Port 2 x-position
PY     = BH / 2  # centreline Y

# ── Analytical Z0 (Hammerstad-Jensen) ─────────────────────────────────────────
def _z0(W, h, er, TT):
    Wu = W + (TT/np.pi)*(1+np.log(2*h/TT)) if TT > 0 else W
    u  = Wu / h
    ef = (er+1)/2 + (er-1)/2*(1+12/u)**-0.5
    return (120*np.pi/np.sqrt(ef))/(u+1.393+0.667*np.log(u+1.444))

Z0_50  = _z0(TW_50, BT, ER, TT)
Z0_B   = _z0(TW_B,  BT, ER, TT)
GAMMA  = (Z0_B - Z0_50) / (Z0_B + Z0_50)
V_P_50 = 3e8 / np.sqrt((4.4+1)/2 + (4.4-1)/2*(1+12*BT/TW_50)**-0.5)
_tag   = "wide" if _WIDE else "narrow"

log(f"50 Ω section  : Z0 = {Z0_50:.1f} Ω  (W={TW_50*1e3:.2f} mm)")
log(f"Section B    : Z0 = {Z0_B:.1f} Ω  (W={TW_B*1e3:.2f} mm, L={L_STEP*1e3:.0f} mm, {_tag})")
log(f"Reflection Γ  = {GAMMA:+.3f}  ({20*np.log10(abs(GAMMA)):.1f} dB)")
log(f"Expected ripple period: Δf = v_p/(2·L) = {V_P_50/(2*L_STEP)/1e9:.2f} GHz")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 1: Build geometry ===")
t0 = time.monotonic()

sim = Simulation(f"microstrip_step_{_tag}", loglevel="WARNING")
sim.set_physics(microwave=True, heatconduction=False)

cu_mat  = Material(cond=5.96e7, name="Copper")
fr4_mat = Material(er=ER, tand=TAND, name="FR4")

stack_layers = [
    PCBLayer(thickness=TT,  material=cu_mat,  name="F.Cu"),
    PCBLayer(thickness=BT,  material=fr4_mat, name="Core"),
    PCBLayer(thickness=TT,  material=cu_mat,  name="B.Cu"),
]
pcb = PCBNew(thickness=BT, unit=1.0, stack=stack_layers,
             layers=2, trace_material=cu_mat)
pcb.set_bounds(0.0, 0.0, BW, BH)

# ── Route: 50Ω lead-in → taper → 100Ω step → taper → 50Ω lead-out ────────────
# straight(distance, width=None) changes width for that segment.
(pcb.new(x=0.0, y=PY, width=TW_50, direction=(1, 0), z=0)
    .straight(PX1)                           # lead-in at 50 Ω
    ["port1"]
    .straight(X_STEP1 - PX1)                 # 50 Ω section up to step 1
    .straight(L_STEP,   width=TW_B)           # section B (narrow or wide)
    .straight(PX2 - X_STEP2)                  # 50 Ω section after step 2
    ["port2"]
    .straight(BW - PX2))                      # lead-out at 50 Ω

pg1 = pcb.lumped_port("port1", name="Port1")
pg2 = pcb.lumped_port("port2", name="Port2")

pml_h  = BT * 4
pml_xy = max(3e-3, min(BW, BH) * 0.15)
pml_z  = max(3e-3, pml_h * 0.5)

try:
    pcb_vol = pcb.generate_pcb(split_z=True, merge=True)
except TypeError:
    pcb_vol = pcb.generate_pcb()

air_vol = pcb.generate_air(height=pml_h)
pml     = open_pml_region(pml_xy, pml_xy, pml_z)

if isinstance(pcb_vol, list):
    for _v in pcb_vol: _v.prio_set(1)
else:
    pcb_vol.prio_set(1)
air_vol.prio_set(5)

sim.commit_geometry(pcb_vol, air_vol, pml, pg1, pg2)
sim.mw.bc.LumpedPort(face=pg1, port_number=1, Z0=50.0)
sim.mw.bc.LumpedPort(face=pg2, port_number=2, Z0=50.0)
sim.mw.bc.assign(AbsorbingBoundary(air_vol.left,  order=2))
sim.mw.bc.assign(AbsorbingBoundary(air_vol.right, order=2))

log(f"  50 Ω  lead-in : x=[0, {X_STEP1*1e3:.0f}] mm  W={TW_50*1e3:.2f} mm")
log(f"  {Z0_B:.0f} Ω step B  : x=[{X_STEP1*1e3:.0f}, {X_STEP2*1e3:.0f}] mm  W={TW_B*1e3:.2f} mm ({_tag})")
log(f"  50 Ω  lead-out : x=[{X_STEP2*1e3:.0f}, {BW*1e3:.0f}] mm  W={TW_50*1e3:.2f} mm")
log(f"Geometry built  ({time.monotonic()-t0:.1f} s)")

if _SHOW_GEOMETRY and gmsh_view_geometry:
    gmsh_view_geometry("Geometry — close window to continue")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 2: Mesh ===")
t1 = time.monotonic()

FMIN, FMAX, NPTS, CPL = 100e6, 6e9, 51, 8
sim.set_resolution(1.0 / CPL)
sim.mw.set_frequency_range(fmin=FMIN, fmax=FMAX, Npoints=NPTS)

import gmsh

MESH_TRACE_MM  = 0.15 if not _WIDE else 0.20   # narrower trace needs finer mesh
MESH_BOARD_MM  = 0.40
MESH_AIR_MM    = 2.00

gmsh.option.setNumber("Mesh.Algorithm",               6)
gmsh.option.setNumber("Mesh.Algorithm3D",            10)
gmsh.option.setNumber("Mesh.CharacteristicLengthMax", MESH_AIR_MM   * 1e-3)
gmsh.option.setNumber("Mesh.CharacteristicLengthMin", MESH_TRACE_MM * 1e-3)
gmsh.option.setNumber("Mesh.Smoothing",              10)
gmsh.option.setNumber("Mesh.MaxNumThreads1D",         0)
gmsh.option.setNumber("Mesh.MaxNumThreads2D",         0)
gmsh.option.setNumber("Mesh.MaxNumThreads3D",         0)

board_z_top = BT + TT; board_z_bot = -TT
_board_surfs = []
for _, _st in gmsh.model.getEntities(2):
    _bb = gmsh.model.getBoundingBox(2, _st)
    if board_z_bot - 0.5e-3 <= (_bb[2]+_bb[5])/2 <= board_z_top + 0.5e-3:
        _board_surfs.append(_st)

# Separate fields: fine on 100Ω section, moderate on 50Ω sections
_step_surfs = [_st for _st in _board_surfs
               if X_STEP1 - 1e-3 <= (_bb_c := gmsh.model.getBoundingBox(2, _st))[0] and
               _bb_c[3] <= X_STEP2 + 1e-3]

if _board_surfs:
    fd = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(fd, "SurfacesList", _board_surfs)
    ft = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(ft, "InField", fd)
    gmsh.model.mesh.field.setNumber(ft, "SizeMin",  MESH_BOARD_MM * 1e-3)
    gmsh.model.mesh.field.setNumber(ft, "SizeMax",  MESH_AIR_MM   * 1e-3)
    gmsh.model.mesh.field.setNumber(ft, "DistMin",  0.0)
    gmsh.model.mesh.field.setNumber(ft, "DistMax",  3e-3)
    active = [ft]

    if _step_surfs:
        fd2 = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(fd2, "SurfacesList", _step_surfs)
        ft2 = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(ft2, "InField", fd2)
        gmsh.model.mesh.field.setNumber(ft2, "SizeMin", MESH_TRACE_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(ft2, "SizeMax", MESH_BOARD_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(ft2, "DistMin", 0.0)
        gmsh.model.mesh.field.setNumber(ft2, "DistMax", TW_B * 2)
        active.append(ft2)

    fm = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(fm, "FieldsList", active)
    gmsh.model.mesh.field.setAsBackgroundMesh(fm)

_stop_hb = threading.Event()
def _hb(stop, t_ref):
    while not stop.wait(20.0):
        log(f"  [mesh] {time.monotonic()-t_ref:.0f} s ...")
_hb_t = threading.Thread(target=_hb, args=(_stop_hb, t1), daemon=True)
_hb_t.start()
try:
    sim.generate_mesh()
finally:
    _stop_hb.set(); _hb_t.join(timeout=3)
log(f"Mesh done  ({time.monotonic()-t1:.1f} s)")

if _SHOW_MESH and gmsh_view_mesh:
    gmsh_view_mesh("Mesh — close window to continue")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 3: FEM sweep ===")
t2 = time.monotonic()

if _CUDSS_AVAILABLE and _SOLVER in ("auto", "gpu", "cuda"):
    sim.mw.solveroutine.set_solver(SolverCuDSS("")); log("  Solver: cuDSS (GPU)")
elif _PARDISO_AVAILABLE and _SOLVER in ("auto", "pardiso"):
    sim.mw.solveroutine.set_solver(SolverPardiso());  log("  Solver: PARDISO")
else:
    sim.mw.solveroutine.set_solver(SolverSuperLU());  log("  Solver: SuperLU (CPU)")

mw_data = sim.mw.run_sweep()
log(f"Sweep done  ({time.monotonic()-t2:.1f} s)")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 4: S-parameters ===")
freq_axis = mw_data.scalar.axis("freq")
M = len(freq_axis)
Smat = np.zeros((M, 2, 2), dtype=complex)
for k, f in enumerate(freq_axis):
    sc = mw_data.scalar.find(freq=f)
    for i in range(2):
        for j in range(2):
            try: Smat[k,i,j] = sc.S(i+1, j+1)
            except Exception: pass

ts_path = _OUTDIR / f"microstrip_step_{_tag}.s2p"
generate_touchstone(
    filename=str(ts_path), freq=np.array(freq_axis),
    Smat=Smat, data_format="RI", funit="GHz",
)
log(f"Touchstone: {ts_path}")

log("\n  freq(GHz)   S11(dB)   S21(dB)")
log("  " + "-"*36)
# Print every 5th point for readability
for k in range(0, M, max(1, M//10)):
    s11 = 20*np.log10(abs(Smat[k,0,0]) + 1e-30)
    s21 = 20*np.log10(abs(Smat[k,1,0]) + 1e-30)
    log(f"  {freq_axis[k]/1e9:8.3f}    {s11:7.2f}    {s21:7.2f}")

log(f"\nDone.  Total: {time.monotonic()-t0:.1f} s")
log(f"Output: {_OUTDIR}")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 4b: TDR (Time Domain Reflectometry) ===")
try:
    import skrf, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freq_hz = np.array(freq_axis)
    nt = skrf.Network(frequency=skrf.Frequency.from_f(freq_hz, unit="Hz"), s=Smat)
    nt_dc  = nt.extrapolate_to_dc()
    N_PAD  = 2048
    nt_rs  = nt_dc.interpolate(
        skrf.Frequency.from_f(np.linspace(0, FMAX, N_PAD), unit="Hz"))
    kw     = np.kaiser(N_PAD, 6)
    S11w   = nt_rs.s[:,0,0] * kw
    S21w   = nt_rs.s[:,1,0] * kw

    tdr_i  = np.fft.ifft(S11w).real
    tdt_i  = np.fft.ifft(S21w).real
    tdr_st = np.cumsum(tdr_i)
    tdt_st = np.cumsum(tdt_i)

    df_rs  = FMAX / (N_PAD - 1)
    dt_rs  = 1.0 / (N_PAD * df_rs)
    C_LIGHT = 3e8

    # Phase velocity for the 50Ω sections (dominant propagation medium)
    _eeff50 = (ER+1)/2 + (ER-1)/2*(1+12*BT/TW_50)**-0.5
    v_p50   = C_LIGHT / np.sqrt(_eeff50)
    dist_mm = np.arange(N_PAD) * dt_rs * v_p50 / 2 * 1e3
    d_res   = (1.0/(FMAX-FMIN)) * v_p50 / 2 * 1e3

    # Expected peak positions (one-way distance from Port1)
    d_step1 = (X_STEP1 - PX1) * 1e3   # mm from Port1 to first step
    d_step2 = (X_STEP2 - PX1) * 1e3   # mm from Port1 to second step

    # ── Plot TDR step + impulse + S-params ──────────────────────────────────
    d_show = (BW - PX1) * 1e3 + 3   # mm: show from Port1 to end of board
    dm     = dist_mm <= d_show

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # S-params vs frequency
    fghz = freq_hz / 1e9
    axes[0].plot(fghz, 20*np.log10(np.abs(Smat[:,0,0])+1e-30), "b-o", ms=3, label="S11")
    axes[0].plot(fghz, 20*np.log10(np.abs(Smat[:,1,0])+1e-30), "r-o", ms=3, label="S21")
    axes[0].axhline(20*np.log10(abs(GAMMA)), color="gray", lw=0.8, ls="--",
                    label=f"Γ={GAMMA:+.3f}")
    axes[0].set(xlabel="f (GHz)", ylabel="dB",
                title=f"S-parameters  (100Ω step, L={L_STEP*1e3:.0f} mm)")
    axes[0].legend(fontsize=8); axes[0].grid(True, alpha=0.4)
    axes[0].set_ylim(-35, 5)

    # TDR step response
    axes[1].plot(dist_mm[dm], tdr_st[dm], "b-", lw=1.5, label="TDR step (S11)")
    for xs, lbl, col in [(d_step1,"→100Ω","red"),(d_step2,"→50Ω","green")]:
        axes[1].axvline(xs, color=col, lw=1.5, ls="--", label=f"{lbl}@{xs:.0f}mm")
    axes[1].set(xlabel=f"dist (mm)  [v_p={v_p50/1e8:.3f}×10⁸ m/s, res≈{d_res:.1f}mm]",
                ylabel="Re(Γ)", title="TDR step response")
    axes[1].legend(fontsize=8); axes[1].grid(True, alpha=0.4)

    # TDR impulse response
    axes[2].plot(dist_mm[dm], tdr_i[dm], "g-", lw=1.5, label="TDR impulse (S11)")
    for xs, col in [(d_step1,"red"),(d_step2,"green")]:
        axes[2].axvline(xs, color=col, lw=1.5, ls="--")
    axes[2].set(xlabel="dist (mm)", ylabel="impulse",
                title=f"TDR impulse  (resolution ≈ {d_res:.1f} mm)")
    axes[2].legend(fontsize=8); axes[2].grid(True, alpha=0.4)

    fig.suptitle(f"Microstrip step: 50Ω → {Z0_B:.0f}Ω → 50Ω  "
                 f"(W: {TW_50*1e3:.2f}mm → {TW_B*1e3:.2f}mm → {TW_50*1e3:.2f}mm, {_tag})",
                 fontsize=11)
    fig.tight_layout()
    p = _OUTDIR / f"step_impedance_{_tag}.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  TDR/S-param plot: {p}")
    log(f"  v_p = {v_p50/1e8:.3f}×10⁸ m/s  (εeff={_eeff50:.3f})")
    log(f"  TDR resolution ≈ {d_res:.1f} mm  (BW={FMAX/1e9:.0f} GHz)")
    log(f"  Expected step1 at {d_step1:.1f} mm, step2 at {d_step2:.1f} mm from Port1")

except Exception as _e:
    log(f"  WARNING: TDR stage failed: {_e}")

if not _PLOTS_ENABLED:
    log("\n=== Stage 5: Field plots (skipped; pass --plots to enable) ===")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 5: Field plots ===")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mid_k  = M // 2
    mid_f  = freq_axis[mid_k]
    fld    = mw_data.field.find(freq=mid_f)
    Z_FCU  = BT; Z_TOP = BT + TT

    def _slice_xy(nx=120, ny=60, z=BT/2):
        xs = np.linspace(0, BW, nx); ys = np.linspace(0, BH, ny)
        XS, YS = np.meshgrid(xs, ys)
        eh = fld.interpolate(XS.ravel(), YS.ravel(), np.full(nx*ny, z))
        E  = np.sqrt(np.abs(eh.Ex)**2+np.abs(eh.Ey)**2+np.abs(eh.Ez)**2).reshape(ny, nx)
        return xs, ys, np.nan_to_num(E)

    xs, ys, Exy = _slice_xy()
    fig, ax = plt.subplots(figsize=(12, 4))
    im = ax.pcolormesh(xs*1e3, ys*1e3, Exy, cmap="hot", shading="auto")
    ax.axvline(X_STEP1*1e3, color="cyan", lw=1.5, ls="--", label=f"50→100Ω")
    ax.axvline(X_STEP2*1e3, color="lime", lw=1.5, ls="--", label=f"100→50Ω")
    plt.colorbar(im, ax=ax, label="|E| (V/m)")
    ax.set(xlabel="X (mm)", ylabel="Y (mm)",
           title=f"|E| XY-slice at z={BT/2*1e3:.1f} mm — {mid_f/1e9:.2f} GHz")
    ax.legend(fontsize=9)
    fig.tight_layout()
    p = _OUTDIR / "E_field_xy.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  Saved: {p}")

except Exception as e:
    log(f"  WARNING: field plots failed: {e}")

log(f"\nAll done.  Total: {time.monotonic()-t0:.1f} s")
