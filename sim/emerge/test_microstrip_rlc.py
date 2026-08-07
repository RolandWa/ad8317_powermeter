"""
Microstrip with mid-line shunt RLC element — EMerge FEM test (shunt-box method).

Approach: inserts a 3D Box with lumped_element_material in the FR4 substrate
directly below the trace midpoint. Current direction = Z (trace→ground).
This models a SHUNT element (parallel to ground), not a series element.
Use test_microstrip_rlc_native.py for the correct series LumpedElement BC approach.

Geometry (all mm):
  Board  : 10 x 5 mm, 1.6 mm thick FR4 (er=4.4, tand=0.02)
  Trace  : 3 mm wide copper, full board length
  Port 1 : left  (x=1 mm, y=2.5 mm)  50 ohm
  Port 2 : right (x=9 mm, y=2.5 mm)  50 ohm
  RLC    : shunt element centred at x=5 mm (substrate column, trace→gnd)

RLC model options (select via CLI):
  --rlc r        Shunt R only         (default: 50 ohm)
  --rlc rc       Shunt R || C         (default: 50 ohm, 10 pF)
  --rlc rl       Shunt series R + L   (default: 50 ohm, 5 nH)
  --rlc rlc      Shunt R || L || C    (default: 50 ohm, 5 nH, 10 pF)

Run:
  python sim/emerge/test_microstrip_rlc.py            # shunt R
  python sim/emerge/test_microstrip_rlc.py --rlc rc
  python sim/emerge/test_microstrip_rlc.py --rlc rl
  python sim/emerge/test_microstrip_rlc.py --rlc rlc  --plots

Output: sim/emerge/results/microstrip_rlc/
"""

import sys, time, pathlib, threading, math
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── CLI ───────────────────────────────────────────────────────────────────────
_RLC_MODE      = "r"
_SOLVER        = "auto"  # override with --solver gpu|pardiso|cpu
_PLOTS_ENABLED = "--plots"         in sys.argv
_SHOW_GEOMETRY = "--show-geometry" in sys.argv
_SHOW_MESH     = "--show-mesh"     in sys.argv
_NO_ABC        = "--no-abc"        in sys.argv
for _i, _a in enumerate(sys.argv[1:], 1):
    if _a == "--rlc"    and _i < len(sys.argv): _RLC_MODE = sys.argv[_i + 1].lower()
    if _a == "--solver" and _i < len(sys.argv): _SOLVER   = sys.argv[_i + 1].lower()

_OUTDIR = pathlib.Path(__file__).parent / "results" / "microstrip_rlc"
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

# ── Geometry (metres) ─────────────────────────────────────────────────────────
BW  = 10e-3   # board X
BH  =  5e-3   # board Y
BT  =  1.6e-3 # FR4 thickness
TT  = 35e-6   # copper thickness
ER  = 4.4
TAND= 0.02
TW_m = 3e-3   # trace width

# Port and RLC x-positions
PX1 = 1e-3    # Port 1
PX2 = 9e-3    # Port 2
PY  = BH / 2
RLC_X = BW / 2  # mid-board (5 mm)

# RLC element body dimensions (0402 footprint equivalent)
RLC_LEN = 0.6e-3   # body length (pad-to-pad gap)
RLC_W   = TW_m     # width = full trace width for best coupling

# ── RLC component values ──────────────────────────────────────────────────────
# Values chosen to produce clearly visible frequency-dependent S-parameter effects
R_VAL = 50.0     # Ω — matches Z0=50Ω for maximum shunt reflection at low freq
C_VAL = 10e-12   # 10 pF — Xc=2.7Ω at 6 GHz: near-short at high freq
L_VAL = 5e-9     # 5  nH — Xl=188Ω at 6 GHz: near-open at high freq

# ── Analytical Z0 (Hammerstad-Jensen) ─────────────────────────────────────────
_Wu = TW_m + (TT / np.pi) * (1 + np.log(2 * BT / TT)) if TT > 0 else TW_m
_ue = _Wu / BT
_eeff = ((ER+1)/2 + (ER-1)/2 * (1 + 12/_ue)**-0.5)
Z0_a = (120*np.pi/np.sqrt(_eeff)) / (_ue + 1.393 + 0.667*np.log(_ue+1.444))
log(f"Analytical microstrip Z0 = {Z0_a:.1f} Ω  (εeff={_eeff:.3f})")

# ── Impedance function — same EMergeConstants API as FreeCAD exporter ──────────
from basicemergesolverhelperpackage.EMergeConstants import series_impedance, parallel_impedance

def _make_z_func():
    if _RLC_MODE == "r":
        log(f"  RLC mode: series_impedance(R={R_VAL} Ω)")
        return series_impedance(R=R_VAL)
    elif _RLC_MODE == "rc":
        log(f"  RLC mode: parallel_impedance(R={R_VAL} Ω, C={C_VAL*1e12:.2g} pF)")
        return parallel_impedance(R=R_VAL, C=C_VAL)
    elif _RLC_MODE == "rl":
        log(f"  RLC mode: series_impedance(R={R_VAL} Ω, L={L_VAL*1e9:.2g} nH)")
        return series_impedance(R=R_VAL, L=L_VAL)
    elif _RLC_MODE == "rlc":
        log(f"  RLC mode: series_impedance(R={R_VAL} Ω, L={L_VAL*1e9:.2g} nH, C={C_VAL*1e12:.2g} pF)")
        return series_impedance(R=R_VAL, L=L_VAL, C=C_VAL)
    else:
        log(f"  Unknown --rlc mode '{_RLC_MODE}' — defaulting to R only")
        return series_impedance(R=R_VAL)

_Z_func = _make_z_func()

# ── Impedance summary at key frequencies ──────────────────────────────────────
log("  Z_component at selected frequencies:")
log("  freq(GHz)   |Z|(Ω)   arg(Z)(°)")
for _f_chk in [0.1e9, 1e9, 3e9, 6e9]:
    _z = _Z_func(_f_chk)
    log(f"    {_f_chk/1e9:5.1f}      {abs(_z):7.2f}   {math.degrees(math.atan2(_z.imag, _z.real)):+7.1f}")

# ─────────────────────────────────────────────────────────────────────────────
log(f"\n=== Stage 1: Build geometry (RLC mode={_RLC_MODE}) ===")
t0 = time.monotonic()

sim = Simulation(f"microstrip_rlc_{_RLC_MODE}", loglevel="WARNING")
sim.set_physics(microwave=True, heatconduction=False)

cu_mat  = Material(cond=5.96e7, name="Copper")
fr4_mat = Material(er=ER, tand=TAND, name="FR4")

# No F.Cu in stack — solid copper fill bypasses any series gap element.
# The trace is built manually as left/right Box segments with a gap between them.
stack_layers = [
    PCBLayer(thickness=BT, material=fr4_mat, name="Core"),
    PCBLayer(thickness=TT, material=cu_mat,  name="B.Cu"),
]
pcb = PCBNew(thickness=BT, unit=1.0, stack=stack_layers,
             layers=1, trace_material=cu_mat)
pcb.set_bounds(0.0, 0.0, BW, BH)

# Trace routing: lumped_element() cuts the trace at the gap and stores the
# series BC polygon — same API as test_microstrip_rlc_native.py but using
# series_impedance / parallel_impedance and the manual-trace-box geometry.
(pcb.new(x=0.0, y=PY, width=TW_m, direction=(1, 0), z=0)
    .straight(PX1)["port1"]
    .straight(RLC_X - RLC_LEN/2 - PX1)
    .lumped_element(_Z_func, size=(RLC_LEN, TW_m))
    .straight(PX2 - (RLC_X + RLC_LEN/2))["port2"]
    .straight(BW - PX2))

pg1 = pcb.lumped_port("port1", name="Port1")
pg2 = pcb.lumped_port("port2", name="Port2")

# ── Manual F.Cu trace: no solid copper fill → no bypass around the gap ────────
from emerge._emerge.geo.shapes import Box as _Box
# Trace height > mesh min-size (0.2 mm) so trace faces AND the polygon at z=0
# are resolved.  Trace TOP at z=0 = polygon level (air interface); same as
# standard PCBNew F.Cu convention where trace top = pcb.top = 0.
TRACE_H = 0.5e-3        # 0.5 mm — resolvable; changes Z0 vs real 35µm PCB trace
_z_trace = pcb.top - TRACE_H   # trace bottom = -TRACE_H; trace top = z=0 (air side)

left_trace = _Box(
    width  = RLC_X - RLC_LEN/2,
    depth  = TW_m,
    height = TRACE_H,
    position = (0.0, PY - TW_m/2, _z_trace),
    name   = "TraceL",
)
left_trace.material = cu_mat
left_trace.prio_set(0)   # overrides Core FR4 in the overlap z=[-TRACE_H,0]

right_trace = _Box(
    width  = BW - (RLC_X + RLC_LEN/2),
    depth  = TW_m,
    height = TRACE_H,
    position = (RLC_X + RLC_LEN/2, PY - TW_m/2, _z_trace),
    name   = "TraceR",
)
right_trace.material = cu_mat
right_trace.prio_set(0)

log(f"  Series {_RLC_MODE.upper()} at x={RLC_X*1e3:.1f} mm  "
    f"(gap={RLC_LEN*1e3:.2f} mm  W={TW_m*1e3:.2f} mm  "
    f"trace_h={TRACE_H*1e3:.1f} mm  polygon at z=0)")

# ── 3D geometry + PML ─────────────────────────────────────────────────────────
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

sim.commit_geometry(pcb_vol, left_trace, right_trace, *pcb.lumped_elements, air_vol, pml, pg1, pg2)

if not _NO_ABC:
    sim.mw.bc.assign(AbsorbingBoundary(air_vol.left,  order=2))
    sim.mw.bc.assign(AbsorbingBoundary(air_vol.right, order=2))
    log("  ABC applied: X=0 and X=BW faces")

log(f"Geometry built  ({time.monotonic()-t0:.1f} s)")

if _SHOW_GEOMETRY and gmsh_view_geometry:
    gmsh_view_geometry("Geometry — close window to continue")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 2: Mesh ===")
t1 = time.monotonic()

FMIN, FMAX, NPTS, CPL = 100e6, 6e9, 51, 8  # 51 pts: df=118 MHz → TDR d_max≈700 mm, resolution≈14 mm
sim.set_resolution(1.0 / CPL)
sim.mw.set_frequency_range(fmin=FMIN, fmax=FMAX, Npoints=NPTS)

import gmsh

MESH_TRACE_MM = 0.20
MESH_BOARD_MM = 0.50
MESH_AIR_MM   = 2.00

gmsh.option.setNumber("Mesh.Algorithm",               6)
gmsh.option.setNumber("Mesh.Algorithm3D",            10)
gmsh.option.setNumber("Mesh.CharacteristicLengthMax", MESH_AIR_MM   * 1e-3)
gmsh.option.setNumber("Mesh.CharacteristicLengthMin", MESH_TRACE_MM * 1e-3)
gmsh.option.setNumber("Mesh.Smoothing",              10)
gmsh.option.setNumber("Mesh.MaxNumThreads1D",         0)
gmsh.option.setNumber("Mesh.MaxNumThreads2D",         0)
gmsh.option.setNumber("Mesh.MaxNumThreads3D",         0)

# Fine mesh around RLC centroid
board_z_top = pcb.top + TRACE_H  # top of trace
board_z_bot = pcb.bottom          # bottom of B.Cu
_board_surfs = []
_rlc_surfs   = []
for _, _stag in gmsh.model.getEntities(2):
    _bb = gmsh.model.getBoundingBox(2, _stag)
    _zc = (_bb[2] + _bb[5]) / 2
    if board_z_bot - 0.5e-3 <= _zc <= board_z_top + 0.5e-3:
        _board_surfs.append(_stag)
        _xc = (_bb[0] + _bb[3]) / 2
        if abs(_xc - RLC_X) < RLC_LEN * 3:
            _rlc_surfs.append(_stag)

if _board_surfs:
    fd = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(fd, "SurfacesList", _board_surfs)
    ft = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(ft, "InField",  fd)
    gmsh.model.mesh.field.setNumber(ft, "SizeMin",  MESH_BOARD_MM * 1e-3)
    gmsh.model.mesh.field.setNumber(ft, "SizeMax",  MESH_AIR_MM   * 1e-3)
    gmsh.model.mesh.field.setNumber(ft, "DistMin",  0.0)
    gmsh.model.mesh.field.setNumber(ft, "DistMax",  3e-3)
    active_fields = [ft]

    if _rlc_surfs:
        fd2 = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(fd2, "SurfacesList", _rlc_surfs)
        ft2 = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(ft2, "InField",  fd2)
        gmsh.model.mesh.field.setNumber(ft2, "SizeMin",  MESH_TRACE_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(ft2, "SizeMax",  MESH_BOARD_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(ft2, "DistMin",  0.0)
        gmsh.model.mesh.field.setNumber(ft2, "DistMax",  RLC_LEN * 4)
        active_fields.append(ft2)
        log(f"  Fine mesh around RLC: {len(_rlc_surfs)} surfaces")

    fm = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(fm, "FieldsList", active_fields)
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
# ── Apply BCs after mesh ──────────────────────────────────────────────────────
sim.mw.bc.LumpedPort(face=pg1, port_number=1, Z0=50.0)
sim.mw.bc.LumpedPort(face=pg2, port_number=2, Z0=50.0)

for _le in pcb.lumped_elements:
    sim.mw.bc.LumpedElement(_le)
log(f"  LumpedElement BCs: {len(pcb.lumped_elements)}")

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
            try:   Smat[k,i,j] = sc.S(i+1, j+1)
            except Exception: pass

ts_path = _OUTDIR / f"microstrip_rlc_{_RLC_MODE}.s2p"
generate_touchstone(
    filename=str(ts_path), freq=np.array(freq_axis),
    Smat=Smat, data_format="RI", funit="GHz",
)
log(f"Touchstone: {ts_path}")

log("\n  freq(GHz)   S11(dB)   S21(dB)  | S11+S21 power (dB) — loss to RLC")
log("  " + "-"*60)
for k, f in enumerate(freq_axis):
    s11 = 20*np.log10(abs(Smat[k,0,0]) + 1e-30)
    s21 = 20*np.log10(abs(Smat[k,1,0]) + 1e-30)
    pwr = 10*np.log10(abs(Smat[k,0,0])**2 + abs(Smat[k,1,0])**2 + 1e-30)
    log(f"  {f/1e9:8.3f}    {s11:7.2f}    {s21:7.2f}   {pwr:8.2f}")

log(f"\nDone.  Total: {time.monotonic()-t0:.1f} s")
log(f"Output: {_OUTDIR}")

# ─────────────────────────────────────────────────────────────
log("\n=== Stage 4b: TDR (Time Domain Reflectometry) ===")
try:
    import skrf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import windows as _win

    # Build skrf Network from FEM S-matrices
    freq_hz = np.array(freq_axis)
    nt_raw = skrf.Network(
        frequency = skrf.Frequency.from_f(freq_hz, unit="Hz"),
        s         = Smat,
    )

    # Interpolate to DC, then resample to power-of-2 for IFFT
    nt_dc  = nt_raw.extrapolate_to_dc()
    N_pad  = 2048                            # zero-pad → smooth time-domain trace
    nt_rs  = nt_dc.interpolate(
        skrf.Frequency.from_f(
            np.linspace(0, FMAX, N_pad), unit="Hz"))

    # Kaiser window (β=6) on S11 and S21 to suppress Gibbs ringing
    _kwin  = np.kaiser(N_pad, 6)
    S11_w  = nt_rs.s[:, 0, 0] * _kwin
    S21_w  = nt_rs.s[:, 1, 0] * _kwin

    # Inverse FFT → impulse response; cumsum → step response (TDR)
    tdr_imp  = np.fft.ifft(S11_w).real
    tdt_imp  = np.fft.ifft(S21_w).real
    tdr_step = np.cumsum(tdr_imp)
    tdt_step = np.cumsum(tdt_imp)

    # Time and distance axes (one-way)
    df_rs   = FMAX / (N_pad - 1)
    dt_rs   = 1.0 / (N_pad * df_rs)         # seconds per IFFT bin
    t_ax    = np.arange(N_pad) * dt_rs
    C_LIGHT = 3e8
    v_p     = C_LIGHT / np.sqrt(_eeff)
    dist_mm = t_ax * v_p / 2 * 1e3          # one-way distance in mm
    d_res   = (1.0/(FMAX - FMIN)) * v_p / 2 * 1e3   # mm per resolution cell

    # ── Plot 1: TDR – S11 step response vs distance ────────────────────
    d_show  = BW*1e3 + 5                      # show a bit beyond board end
    d_mask  = dist_mm <= d_show
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7), sharex=True)
    ax1.plot(dist_mm[d_mask], tdr_step[d_mask], "b-", lw=1.5, label="TDR S11 (step)")
    ax1.axvline(RLC_X*1e3,     color="red",  lw=1.5, ls="--", label=f"RLC @ {RLC_X*1e3:.0f} mm")
    ax1.axvline(PX1*1e3,       color="gray", lw=0.8, ls=":" , label=f"Port1 @ {PX1*1e3:.0f} mm")
    ax1.axvline(PX2*1e3,       color="gray", lw=0.8, ls=":" , label=f"Port2 @ {PX2*1e3:.0f} mm")
    ax1.set_ylabel("Re(Γ) step"); ax1.legend(fontsize=8); ax1.grid(True, alpha=0.4)
    ax1.set_title(f"TDR — {_RLC_MODE.upper()} load  "
                  f"(R={R_VAL:.0f}Ω  L={L_VAL*1e9:.1f}nH  C={C_VAL*1e12:.1f}pF)  "
                  f"resolution≈{d_res:.1f} mm")

    ax2.plot(dist_mm[d_mask], tdt_step[d_mask], "r-", lw=1.5, label="TDT S21 (step)")
    ax2.axvline(RLC_X*1e3, color="red",  lw=1.5, ls="--")
    ax2.set_xlabel(f"One-way distance (mm)   [v_p = {v_p/1e8:.3f}×10⁸ m/s, εeff={_eeff:.3f}]")
    ax2.set_ylabel("Re(T) step"); ax2.legend(fontsize=8); ax2.grid(True, alpha=0.4)
    ax2.set_xlim(0, d_show)
    fig.tight_layout()
    p = _OUTDIR / f"TDR_{_RLC_MODE}.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  TDR saved: {p}")

    # ── Plot 2: S-params vs frequency + TDR impulse side-by-side ─────────
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    fghz = freq_hz / 1e9
    axes[0].plot(fghz, 20*np.log10(np.abs(Smat[:,0,0])+1e-30), "b-o", ms=3, label="S11")
    axes[0].plot(fghz, 20*np.log10(np.abs(Smat[:,1,0])+1e-30), "r-o", ms=3, label="S21")
    axes[0].set(xlabel="f (GHz)", ylabel="dB", title="S-parameters")
    axes[0].legend(); axes[0].grid(True, alpha=0.4)

    axes[1].plot(dist_mm[d_mask], tdr_step[d_mask], "b-", lw=1.5)
    axes[1].axvline(RLC_X*1e3, color="red", lw=1.5, ls="--", label=f"RLC")
    axes[1].set(xlabel="dist (mm)", ylabel="Re(Γ)", title="TDR step (S11)"); axes[1].legend(); axes[1].grid(True, alpha=0.4)

    axes[2].plot(dist_mm[d_mask], tdr_imp[d_mask], "g-", lw=1.5)
    axes[2].axvline(RLC_X*1e3, color="red", lw=1.5, ls="--", label=f"RLC")
    axes[2].set(xlabel="dist (mm)", ylabel="impulse", title="TDR impulse (S11)"); axes[2].legend(); axes[2].grid(True, alpha=0.4)
    fig.suptitle(f"{_RLC_MODE.upper()} load: R={R_VAL:.0f}Ω  L={L_VAL*1e9:.1f}nH  C={C_VAL*1e12:.1f}pF")
    fig.tight_layout()
    p2 = _OUTDIR / f"TDR_overview_{_RLC_MODE}.png"
    fig.savefig(p2, dpi=150); plt.close(fig)
    log(f"  TDR overview saved: {p2}")

    log(f"  v_phase = {v_p/1e8:.3f}×10⁸ m/s  (εeff={_eeff:.3f})")
    log(f"  dt={dt_rs*1e12:.1f} ps/bin  Δd_resolution={d_res:.1f} mm  (BW={FMAX/1e9:.1f} GHz)")
except Exception as _tdr_exc:
    log(f"  WARNING: TDR stage failed: {_tdr_exc}")

if not _PLOTS_ENABLED:
    log("\n=== Stage 5: Field plots (skipped; pass --plots to enable) ===")
    sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 5: Field plots ===")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mid_k = M // 2
    mid_f = freq_axis[mid_k]
    fld_mid = mw_data.field.find(freq=mid_f)

    Z_FCU  = BT
    Z_TTOP = BT + TT

    def _slice_xz(nx=80, nz=50, y=PY):
        xs = np.linspace(0, BW, nx)
        zs = np.linspace(-2e-3, Z_TTOP + 2e-3, nz)
        XS, ZS = np.meshgrid(xs, zs)
        eh = fld_mid.interpolate(XS.ravel(), np.full(nx*nz, y), ZS.ravel())
        Emag = np.sqrt(np.abs(eh.Ex)**2 + np.abs(eh.Ey)**2 + np.abs(eh.Ez)**2).reshape(nz, nx)
        return xs, zs, np.nan_to_num(Emag)

    def _slice_xy(nx=80, ny=60, z=BT/2):
        xs = np.linspace(0, BW, nx)
        ys = np.linspace(0, BH, ny)
        XS, YS = np.meshgrid(xs, ys)
        eh = fld_mid.interpolate(XS.ravel(), YS.ravel(), np.full(nx*ny, z))
        Emag = np.sqrt(np.abs(eh.Ex)**2 + np.abs(eh.Ey)**2 + np.abs(eh.Ez)**2).reshape(ny, nx)
        return xs, ys, np.nan_to_num(Emag)

    # XZ plane — E field magnitude
    xs, zs, Emag_xz = _slice_xz()
    fig, ax = plt.subplots(figsize=(9, 4))
    im = ax.pcolormesh(xs*1e3, zs*1e3, Emag_xz, cmap="hot", shading="auto")
    ax.axvline(RLC_X*1e3, color="cyan", lw=1.5, label=f"RLC ({_RLC_MODE.upper()})")
    ax.axhline(Z_FCU*1e3, color="blue", lw=0.8, ls="--", label="F.Cu")
    ax.axhline(0,          color="green",lw=0.8, ls="--", label="B.Cu")
    plt.colorbar(im, ax=ax, label="|E| (V/m)")
    ax.set(xlabel="X (mm)", ylabel="Z (mm)",
           title=f"|E| XZ-slice at Y={PY*1e3:.1f} mm — {mid_f/1e9:.2f} GHz  [{_RLC_MODE.upper()}]")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = _OUTDIR / f"E_xz_{_RLC_MODE}.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  Saved: {p}")

    # XY plane — E field magnitude at mid-substrate
    xs, ys, Emag_xy = _slice_xy()
    fig, ax = plt.subplots(figsize=(9, 5))
    im = ax.pcolormesh(xs*1e3, ys*1e3, Emag_xy, cmap="hot", shading="auto")
    ax.axvline(RLC_X*1e3, color="cyan", lw=1.5, label=f"RLC ({_RLC_MODE.upper()})")
    plt.colorbar(im, ax=ax, label="|E| (V/m)")
    ax.set(xlabel="X (mm)", ylabel="Y (mm)",
           title=f"|E| XY-slice at Z={BT/2*1e3:.2f} mm (mid-sub) — {mid_f/1e9:.2f} GHz  [{_RLC_MODE.upper()}]")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = _OUTDIR / f"E_xy_{_RLC_MODE}.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  Saved: {p}")

    # S-parameter vs frequency plot
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    freqs_ghz = np.array(freq_axis) / 1e9
    s11_db = 20*np.log10(np.abs(Smat[:,0,0]) + 1e-30)
    s21_db = 20*np.log10(np.abs(Smat[:,1,0]) + 1e-30)
    ax1.plot(freqs_ghz, s11_db, "b-o", ms=4, label="S11"); ax1.set_ylabel("S11 (dB)")
    ax1.axhline(-10, color="k", lw=0.8, ls="--"); ax1.grid(True, alpha=0.4)
    ax2.plot(freqs_ghz, s21_db, "r-o", ms=4, label="S21"); ax2.set_ylabel("S21 (dB)")
    ax2.set_xlabel("Frequency (GHz)"); ax2.grid(True, alpha=0.4)
    for ax in (ax1, ax2):
        ax.legend(fontsize=9)
        ax.axvline(mid_f/1e9, color="gray", lw=0.8, ls=":")
    fig.suptitle(f"Microstrip + mid-line {_RLC_MODE.upper()} load  "
                 f"(R={R_VAL:.0f}Ω  L={L_VAL*1e9:.1f}nH  C={C_VAL*1e12:.1f}pF)")
    fig.tight_layout()
    p = _OUTDIR / f"S_params_{_RLC_MODE}.png"
    fig.savefig(p, dpi=150); plt.close(fig)
    log(f"  Saved: {p}")

except Exception as exc:
    log(f"  WARNING: field plots failed: {exc}")

log(f"\nAll done.  Total: {time.monotonic()-t0:.1f} s")
