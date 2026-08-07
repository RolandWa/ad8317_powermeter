"""
Minimal EMerge FEM test — straight microstrip on FR4.

Geometry (all mm):
  Board  : 10 x 5 mm, 1.6 mm thick FR4 (er=4.4, tand=0.02)
  Trace  : 3 mm wide copper, full board length (simplified — no Gerbers)
  Port 1 : left  (x=1 mm, y=2.5 mm)  50 ohm
  Port 2 : right (x=9 mm, y=2.5 mm)  50 ohm

Targets:
  * Mesh + FEM sweep  (100 MHz - 6 GHz, 11 pts)
  * Write .s2p Touchstone
    * Optional field/plot exports when --plots is provided

Run:
  cd <repo_root>
  python sim/emerge/test_simple_microstrip.py
    python sim/emerge/test_simple_microstrip.py --plots
Output: sim/emerge/results/simple_test/
"""

import sys, time, pathlib, threading
import numpy as np

# UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_USE_ABC = "--no-abc" not in sys.argv   # pass --no-abc to disable absorbing BCs
_PLOTS_ENABLED = "--plots" in sys.argv   # pass --plots to enable Stage 5 plotting
_SHOW_GEOMETRY = "--show-geometry" in sys.argv
_SHOW_MESH = "--show-mesh" in sys.argv

_label  = "with_abc" if _USE_ABC else "no_abc"
_OUTDIR = pathlib.Path(__file__).parent / "results" / "simple_test"
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
        gmsh_view_geometry = None
        gmsh_view_mesh = None
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


# ── Analytical microstrip impedance (Hammerstad-Jensen) ───────────────────────
TW_m  = 3e-3      # trace width
_u    = TW_m / BT   # W/h
_t_h  = TT / BT    # t/h — copper thickness correction
# Effective width including thickness correction (IPC-2141A)
_Wu   = TW_m + (TT / np.pi) * (1 + np.log(2 * BT / TT)) if TT > 0 else TW_m
_ue   = _Wu / BT
# Effective permittivity
if _ue <= 1:
    _eeff = (ER + 1)/2 + (ER - 1)/2 * ((1 + 12/_ue)**-0.5 + 0.04*(1-_ue)**2)
else:
    _eeff = (ER + 1)/2 + (ER - 1)/2 * (1 + 12/_ue)**-0.5
# Characteristic impedance
if _ue <= 1:
    Z0_analytical = (60 / np.sqrt(_eeff)) * np.log(8/_ue + _ue/4)
else:
    Z0_analytical = (120 * np.pi / np.sqrt(_eeff)) / (_ue + 1.393 + 0.667*np.log(_ue + 1.444))
log(f"Analytical Z0 = {Z0_analytical:.1f} Ω  (W={TW_m*1e3:.1f} mm, h={BT*1e3:.1f} mm, "
    f"εr={ER}, εeff={_eeff:.3f})")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 1: Build geometry ===")
t0 = time.monotonic()

sim = Simulation("microstrip_test", loglevel="WARNING")
sim.set_physics(microwave=True, heatconduction=False)

cu_mat  = Material(cond=5.96e7, name="Copper")
fr4_mat = Material(er=ER, tand=TAND, name="FR4")

stack_layers = [
    PCBLayer(thickness=TT,  material=cu_mat,  name="F.Cu"),
    PCBLayer(thickness=BT,  material=fr4_mat, name="Core"),
    PCBLayer(thickness=TT,  material=cu_mat,  name="B.Cu"),
]

pcb = PCBNew(
    thickness      = BT,
    unit           = 1.0,
    stack          = stack_layers,
    layers         = 2,           # number of copper layers
    trace_material = cu_mat,
)
pcb.set_bounds(0.0, 0.0, BW, BH)

# Route the microstrip trace on F.Cu (z=0 = top copper layer in PCBNew local Z)
# Direction (1,0) = +X.  Tag endpoints at port positions so lumped_port()
# can read width/direction from the StripLine.
px1 = 1e-3                   # Port 1 at x=1 mm
px2 = 9e-3                   # Port 2 at x=9 mm
py  = BH / 2                 # centred in Y

(pcb.new(x=0.0, y=py, width=TW_m, direction=(1, 0), z=0)
    .straight(px1)           # lead-in: x=0 → x=px1
    ["port1"]                # store StripLine at x=px1 as "port1"
    .straight(px2 - px1)     # main trace: x=px1 → x=px2
    ["port2"]                # store StripLine at x=px2 as "port2"
    .straight(BW - px2))     # lead-out: x=px2 → x=BW

port_z     = BT + TT         # trace top (annotation only)
port_z_gnd = 0.0             # B.Cu in EMerge global Z (for field plot annotations)

# z_ground=None → default = -BT (bottom of FR4 in PCBNew local Z)
# port height = 0 − (−BT) = BT = substrate thickness ✓
pg1 = pcb.lumped_port("port1", name="Port1")
pg2 = pcb.lumped_port("port2", name="Port2")

# Build 3D geometry
pml_h  = BT * 4
pml_xy = max(3e-3, min(BW, BH) * 0.15)
pml_z  = max(3e-3, pml_h * 0.5)

try:
    pcb_vol = pcb.generate_pcb(split_z=True, merge=True)
except TypeError:
    pcb_vol = pcb.generate_pcb()

air_vol = pcb.generate_air(height=pml_h)
pml     = open_pml_region(pml_xy, pml_xy, pml_z)

sim.commit_geometry(pcb_vol, air_vol, pml, pg1, pg2)

# Lumped port BCs
sim.mw.bc.LumpedPort(face=pg1, port_number=1, Z0=50.0)
sim.mw.bc.LumpedPort(face=pg2, port_number=2, Z0=50.0)

# ── Absorbing boundary conditions at the trace ends (X=0 and X=BW) ───────────
if _USE_ABC:
    abc_x0  = AbsorbingBoundary(air_vol.left,  order=2)   # X = 0  (trace start)
    abc_xBW = AbsorbingBoundary(air_vol.right, order=2)   # X = BW (trace end)
    sim.mw.bc.assign(abc_x0)
    sim.mw.bc.assign(abc_xBW)
    log("  ABC applied: X=0 and X=BW faces")
else:
    log("  ABC disabled (--no-abc)")

log(f"Geometry built  ({time.monotonic()-t0:.1f} s)")

if _SHOW_GEOMETRY:
    if gmsh_view_geometry is not None:
        log("Showing geometry viewer — close window to continue ...")
        gmsh_view_geometry("Geometry — close window to continue")
    else:
        log("Geometry viewer not available (gmsh_view_geometry import failed).")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 2: Mesh ===")
t1 = time.monotonic()

# Frequency range + resolution MUST be set before generate_mesh()
# so EMerge can compute cells-per-lambda mesh sizing.
FMIN, FMAX, NPTS, CPL = 100e6, 6e9, 5, 8   # 5 pts, CPL=8 → coarser mesh
sim.set_resolution(1.0 / CPL)
sim.mw.set_frequency_range(fmin=FMIN, fmax=FMAX, Npoints=NPTS)

import gmsh

# ── Mesh sizes ────────────────────────────────────────────────────────────────
# Priority: trace/port surfaces (fine) > board substrate > air box (coarse)
# Using GMSH Distance+Threshold fields to grade from fine near board to coarse
# in air — avoids wasting elements far from the signal path.
MESH_TRACE_MM  = 0.30   # fine mesh on copper trace and port faces
MESH_BOARD_MM  = 0.60   # substrate region
MESH_AIR_MM    = 2.00   # air/PML — coarse, fields weak here
MESH_TRANS_MM  = 3.0    # transition distance from board surface to air size

gmsh.option.setNumber("Mesh.Algorithm",               6)    # Frontal-Delaunay 2D
gmsh.option.setNumber("Mesh.Algorithm3D",            10)    # HXT — all cores
gmsh.option.setNumber("Mesh.CharacteristicLengthMax", MESH_AIR_MM * 1e-3)
gmsh.option.setNumber("Mesh.CharacteristicLengthMin", MESH_TRACE_MM * 1e-3)
gmsh.option.setNumber("Mesh.Smoothing",              10)
gmsh.option.setNumber("Mesh.MaxNumThreads1D",         0)    # all cores
gmsh.option.setNumber("Mesh.MaxNumThreads2D",         0)
gmsh.option.setNumber("Mesh.MaxNumThreads3D",         0)

# Distance field from ALL surfaces within the board Z range — this includes
# copper and substrate surfaces, so size grades outward into air.
board_z_top = BT + TT    # top of trace (m)
board_z_bot = -TT        # bottom of B.Cu

# Collect all surface tags that lie within the board Z band
_board_surfs = []
for _, _stag in gmsh.model.getEntities(2):
    _bb = gmsh.model.getBoundingBox(2, _stag)
    _z_centre = (_bb[2] + _bb[5]) / 2
    if board_z_bot - 0.5e-3 <= _z_centre <= board_z_top + 0.5e-3:
        _board_surfs.append(_stag)

if _board_surfs:
    # Field 1: Distance from board surfaces
    f_dist = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(f_dist, "SurfacesList", _board_surfs)

    # Field 2: Threshold — fine at board, coarse at distance MESH_TRANS_MM
    f_thr = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(f_thr, "InField",   f_dist)
    gmsh.model.mesh.field.setNumber(f_thr, "SizeMin",   MESH_BOARD_MM * 1e-3)
    gmsh.model.mesh.field.setNumber(f_thr, "SizeMax",   MESH_AIR_MM   * 1e-3)
    gmsh.model.mesh.field.setNumber(f_thr, "DistMin",   0.0)
    gmsh.model.mesh.field.setNumber(f_thr, "DistMax",   MESH_TRANS_MM * 1e-3)

    # Field 3: Distance from port/trace surfaces only (fine priority)
    _trace_surfs = []
    TW = 3e-3   # trace width (m)
    trace_y_lo = (BH - TW) / 2 - 0.5e-3
    trace_y_hi = (BH + TW) / 2 + 0.5e-3
    for _stag in _board_surfs:
        _bb = gmsh.model.getBoundingBox(2, _stag)
        _yc = (_bb[1] + _bb[4]) / 2
        if trace_y_lo <= _yc <= trace_y_hi:
            _trace_surfs.append(_stag)

    if _trace_surfs:
        f_tdist = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(f_tdist, "SurfacesList", _trace_surfs)

        f_tthr = gmsh.model.mesh.field.add("Threshold")
        gmsh.model.mesh.field.setNumber(f_tthr, "InField",   f_tdist)
        gmsh.model.mesh.field.setNumber(f_tthr, "SizeMin",   MESH_TRACE_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(f_tthr, "SizeMax",   MESH_BOARD_MM * 1e-3)
        gmsh.model.mesh.field.setNumber(f_tthr, "DistMin",   0.0)
        gmsh.model.mesh.field.setNumber(f_tthr, "DistMax",   TW * 0.5)  # half trace width

        # Min field: take smallest size from board-threshold and trace-threshold
        f_min = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(f_min, "FieldsList", [f_thr, f_tthr])
        gmsh.model.mesh.field.setAsBackgroundMesh(f_min)
        log(f"  Mesh fields: trace={MESH_TRACE_MM} mm  board={MESH_BOARD_MM} mm  "
            f"air={MESH_AIR_MM} mm  ({len(_trace_surfs)} trace surfs, "
            f"{len(_board_surfs)} board surfs)")
    else:
        gmsh.model.mesh.field.setAsBackgroundMesh(f_thr)
        log(f"  Mesh fields: board={MESH_BOARD_MM} mm  air={MESH_AIR_MM} mm  "
            f"({len(_board_surfs)} board surfs, no trace surfs found)")
else:
    log("  Mesh fields: no board surfaces found — using global CLmax")

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

if _SHOW_MESH:
    if gmsh_view_mesh is not None:
        log("Showing mesh viewer — close window to continue ...")
        gmsh_view_mesh("Mesh — close window to continue")
    else:
        log("Mesh viewer not available (gmsh_view_mesh import failed).")

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 3: FEM sweep ===")
t2 = time.monotonic()

if _CUDSS_AVAILABLE:
    sim.mw.solveroutine.set_solver(SolverCuDSS(""))
    log("  Solver: cuDSS (GPU)")
elif _PARDISO_AVAILABLE:
    sim.mw.solveroutine.set_solver(SolverPardiso())
    log("  Solver: PARDISO")
else:
    sim.mw.solveroutine.set_solver(SolverSuperLU())
    log("  Solver: SuperLU")

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

ts_path = _OUTDIR / f"microstrip_test_{_label}.s2p"
generate_touchstone(
    filename=str(ts_path), freq=np.array(freq_axis),
    Smat=Smat, data_format="RI", funit="GHz",
)
log(f"Touchstone: {ts_path}")

log("\n  freq(GHz)   S11(dB)   S21(dB)")
log("  " + "-"*36)
for k, f in enumerate(freq_axis):
    s11 = 20*np.log10(abs(Smat[k,0,0]) + 1e-30)
    s21 = 20*np.log10(abs(Smat[k,1,0]) + 1e-30)
    log(f"  {f/1e9:8.3f}    {s11:7.2f}    {s21:7.2f}")

# ── Simulated Z0 from port impedances ─────────────────────────────────────────
Z0_sim = np.zeros((M, 2), dtype=complex)
beta_sim = np.zeros(M)
for k, f in enumerate(freq_axis):
    sc = mw_data.scalar.find(freq=f)
    try:
        _z0 = sc.Z0   # shape (n_ports,) or scalar
        _z0 = np.atleast_1d(_z0)
        Z0_sim[k, :min(2, len(_z0))] = _z0[:2]
    except Exception:
        pass
    try:
        beta_sim[k] = float(np.real(sc.beta))
    except Exception:
        pass

# ── Z0 extraction from S-parameters ──────────────────────────────────────────
# Z0_port (reference) is 50 Ω. With port 2 terminated at Z0_port:
#   Z_in = Z0_port * (1 + S11) / (1 - S11)
# For a lossless matched line this equals the line impedance.
# Also estimate εeff and β from the S21 phase:
#   θ = -angle(S21)  → β = θ / line_length  → εeff = (β/k0)²
_Lline = BW - 2 * px1   # port-to-port distance ≈ board length minus port offsets
_Lline = max(_Lline, BW * 0.8)  # fallback: 80 % of board length
Z0_extracted  = np.zeros(M, dtype=complex)
eeff_extracted = np.zeros(M)
beta_extracted = np.zeros(M)
for k, f in enumerate(freq_axis):
    S11 = Smat[k, 0, 0]; S21 = Smat[k, 1, 0]
    Z_ref = 50.0
    denom = 1 - S11
    if abs(denom) > 1e-10:
        Z0_extracted[k] = Z_ref * (1 + S11) / denom
    phase = -np.angle(S21)            # electrical length θ (rad)
    if phase != 0 and f > 0:
        k0 = 2 * np.pi * f / 3e8
        b  = phase / _Lline
        beta_extracted[k] = b
        eeff_extracted[k] = (b / k0)**2 if k0 > 0 else 0.0

log(f"\n  Analytical Z0 = {Z0_analytical:.1f} Ω  (εeff = {_eeff:.3f})")
log(f"  Line length used for β extraction: {_Lline*1e3:.1f} mm")
log("\n  Simulated Z0 extracted from S-parameters:")
log("  freq(GHz)   Re(Z0) Ω   Im(Z0) Ω   |Z0| Ω   εeff_sim   β (rad/m)")
log("  " + "-"*70)
for k, f in enumerate(freq_axis):
    z = Z0_extracted[k]
    log(f"  {f/1e9:8.3f}    {z.real:9.2f}   {z.imag:9.2f}   {abs(z):7.2f}"
        f"   {eeff_extracted[k]:8.4f}   {beta_extracted[k]:10.1f}")

    if not _PLOTS_ENABLED:
        log("\n=== Stage 5: Field plots (skipped; pass --plots to enable) ===")
        log(f"\nDone.  Total: {time.monotonic()-t0:.1f} s")
        log(f"Output: {_OUTDIR}")
        sys.exit(0)

# ─────────────────────────────────────────────────────────────────────────────
log("\n=== Stage 5: Field plots ===")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D   # noqa: F401

    mid_k = M // 2
    mid_f = freq_axis[mid_k]

    # ── Field data via mw_data.field (MWField.interpolate) ────────────────────
    fld_mid = mw_data.field.find(freq=mid_f)
    log(f"  Field dataset at {mid_f/1e9:.2f} GHz  (freq axis len={M})")

    # EMerge Z reference: port_z_gnd=0 → B.Cu ground at z=0
    #   B.Cu ground plane : z = 0
    #   FR4 substrate     : z = 0 … BT
    #   F.Cu bottom       : z = BT
    #   F.Cu / trace top  : z = BT + TT  (= port_z)
    Z_BCU  = 0.0       # ground plane
    Z_FCU  = BT        # F.Cu bottom (top of FR4)
    Z_TTOP = BT + TT   # trace top = port_z

    def _interp_slice_xz(nx=80, nz=50, y=BH/2):
        """Return |E|, |H| on an XZ meshgrid at fixed Y (vectorised)."""
        xs = np.linspace(0, BW, nx)
        zs = np.linspace(-2e-3, Z_TTOP + 2e-3, nz)   # air below GND … air above trace
        XS, ZS = np.meshgrid(xs, zs)          # shape (nz, nx)
        xf = XS.ravel(); zf = ZS.ravel()
        yf = np.full_like(xf, y)
        eh = fld_mid.interpolate(xf, yf, zf)
        Emag = np.sqrt(np.abs(eh.Ex)**2 + np.abs(eh.Ey)**2 + np.abs(eh.Ez)**2).reshape(nz, nx)
        Hmag = np.sqrt(np.abs(eh.Hx)**2 + np.abs(eh.Hy)**2 + np.abs(eh.Hz)**2).reshape(nz, nx)
        Emag = np.nan_to_num(Emag); Hmag = np.nan_to_num(Hmag)
        return xs, zs, Emag, Hmag

    def _interp_slice_xy(nx=80, ny=60, z=BT/2):
        """Return |E|, |H| on an XY meshgrid at fixed Z (vectorised)."""
        xs = np.linspace(0, BW, nx)
        ys = np.linspace(0, BH, ny)
        XS, YS = np.meshgrid(xs, ys)          # shape (ny, nx)
        xf = XS.ravel(); yf = YS.ravel()
        zf = np.full_like(xf, z)
        eh = fld_mid.interpolate(xf, yf, zf)
        Emag = np.sqrt(np.abs(eh.Ex)**2 + np.abs(eh.Ey)**2 + np.abs(eh.Ez)**2).reshape(ny, nx)
        Hmag = np.sqrt(np.abs(eh.Hx)**2 + np.abs(eh.Hy)**2 + np.abs(eh.Hz)**2).reshape(ny, nx)
        Emag = np.nan_to_num(Emag); Hmag = np.nan_to_num(Hmag)
        return xs, ys, Emag, Hmag

    y_sl = BH / 2
    xs_xz, zs_xz, E_xz, H_xz = _interp_slice_xz(y=y_sl)
    log(f"  XZ slice: |E| max={E_xz.max():.3e} V/m  |H| max={H_xz.max():.3e} A/m")
    xs_xy, ys_xy, E_xy, H_xy = _interp_slice_xy(z=BT/2)   # mid-FR4
    log(f"  XY slice: |E| max={E_xy.max():.3e} V/m  |H| max={H_xy.max():.3e} A/m")

    # ── Helper: annotate copper layer lines on XZ axes ────────────────────────
    def _xz_layers(ax):
        ax.axhline(Z_BCU *1e3, color="blue",   lw=1.5, ls="--", label="B.Cu (GND)")
        ax.axhline(Z_FCU *1e3, color="cyan",   lw=1.5, ls="--", label="F.Cu bottom")
        ax.axhline(Z_TTOP*1e3, color="yellow", lw=1,   ls=":",  label="trace top")

    # ── E-field XZ PNG ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(E_xz, extent=[0, BW*1e3, zs_xz[0]*1e3, zs_xz[-1]*1e3],
                   origin="lower", aspect="auto", cmap="hot")
    _xz_layers(ax)
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Z (mm)")
    ax.set_title(f"|E| at {mid_f/1e9:.2f} GHz  (XZ slice, Y={y_sl*1e3:.1f} mm)")
    plt.colorbar(im, ax=ax, label="|E| (V/m)"); ax.legend(loc="upper right", fontsize=8)
    p = _OUTDIR / "E_field_XZ.png"; fig.savefig(str(p), dpi=150, bbox_inches="tight"); plt.close(fig)
    log(f"  E-field XZ: {p}")

    # ── H-field XZ PNG ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(11, 5))
    im = ax.imshow(H_xz, extent=[0, BW*1e3, zs_xz[0]*1e3, zs_xz[-1]*1e3],
                   origin="lower", aspect="auto", cmap="plasma")
    _xz_layers(ax)
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Z (mm)")
    ax.set_title(f"|H| at {mid_f/1e9:.2f} GHz  (XZ slice, Y={y_sl*1e3:.1f} mm)")
    plt.colorbar(im, ax=ax, label="|H| (A/m)"); ax.legend(loc="upper right", fontsize=8)
    p = _OUTDIR / "H_field_XZ.png"; fig.savefig(str(p), dpi=150, bbox_inches="tight"); plt.close(fig)
    log(f"  H-field XZ: {p}")

    # ── H-field XY PNG ────────────────────────────────────────────────────────
    TW_m = 3e-3
    fig, ax = plt.subplots(figsize=(11, 6))
    im = ax.imshow(H_xy, extent=[0, BW*1e3, 0, BH*1e3],
                   origin="lower", aspect="auto", cmap="plasma")
    ax.axhline(y_sl*1e3,               color="white", lw=1.5, ls="--", label="trace centre")
    ax.axhline((y_sl - TW_m/2)*1e3,    color="cyan",  lw=1,   ls=":",  label="trace edge")
    ax.axhline((y_sl + TW_m/2)*1e3,    color="cyan",  lw=1,   ls=":")
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)")
    ax.set_title(f"|H| at {mid_f/1e9:.2f} GHz  (XY slice, Z={BT/2*1e3:.2f} mm  mid-FR4)")
    plt.colorbar(im, ax=ax, label="|H| (A/m)"); ax.legend(loc="upper right", fontsize=8)
    p = _OUTDIR / "H_field_XY.png"; fig.savefig(str(p), dpi=150, bbox_inches="tight"); plt.close(fig)
    log(f"  H-field XY: {p}")

    # ── E-field 3D scatter PNG (matplotlib) ───────────────────────────────────
    nx3, ny3, nz3 = 35, 20, 20
    xs3 = np.linspace(0, BW, nx3)
    ys3 = np.linspace(0, BH, ny3)
    zs3 = np.linspace(-2e-3, Z_TTOP + 2e-3, nz3)
    X3, Y3, Z3 = np.meshgrid(xs3, ys3, zs3, indexing="ij")
    xf3 = X3.ravel(); yf3 = Y3.ravel(); zf3 = Z3.ravel()
    eh3 = fld_mid.interpolate(xf3, yf3, zf3)
    E3  = np.nan_to_num(np.sqrt(np.abs(eh3.Ex)**2 + np.abs(eh3.Ey)**2 + np.abs(eh3.Ez)**2))
    log(f"  3D grid: max |E|={E3.max():.3e}  non-zero={np.sum(E3>0)}/{E3.size}")

    thresh = float(np.percentile(E3[E3 > 0], 60)) if np.any(E3 > 0) else 0.0
    mask3  = E3 >= thresh
    xp = xf3[mask3]*1e3; yp = yf3[mask3]*1e3; zp = zf3[mask3]*1e3; cp = E3[mask3]

    fig3d = plt.figure(figsize=(12, 7))
    ax3d  = fig3d.add_subplot(111, projection="3d")
    if cp.size > 0:
        sc3 = ax3d.scatter(xp, yp, zp, c=cp, cmap="hot", s=5, alpha=0.5,
                           vmin=cp.min(), vmax=cp.max())
        plt.colorbar(sc3, ax=ax3d, label="|E| (V/m)", shrink=0.55, pad=0.12)
    bx = [0, BW*1e3, BW*1e3, 0, 0]; by = [0, 0, BH*1e3, BH*1e3, 0]
    for zl, lc, ll in [(Z_BCU*1e3, "blue", "B.Cu (GND)"), (Z_FCU*1e3, "cyan", "F.Cu"),
                       (Z_TTOP*1e3, "yellow", "trace top")]:
        ax3d.plot(bx, by, [zl]*5, color=lc, lw=1.2, label=ll)
    ax3d.set_xlabel("X (mm)"); ax3d.set_ylabel("Y (mm)"); ax3d.set_zlabel("Z (mm)")
    ax3d.set_title(f"|E| 3D at {mid_f/1e9:.2f} GHz")
    ax3d.legend(loc="upper left", fontsize=8); ax3d.view_init(elev=22, azim=-55)
    p = _OUTDIR / "E_field_3D.png"; fig3d.savefig(str(p), dpi=150, bbox_inches="tight"); plt.close(fig3d)
    log(f"  E-field 3D PNG: {p}")

    # ── E-field interactive 3D HTML (Plotly) ──────────────────────────────────
    try:
        import plotly.graph_objects as go

        traces = []

        # E-field volume scatter — all non-NaN points coloured by magnitude
        valid = E3 > 0
        xpa = xf3[valid]*1e3; ypa = yf3[valid]*1e3; zpa = zf3[valid]*1e3; cpa = E3[valid]
        if cpa.size > 0:
            traces.append(go.Scatter3d(
                x=xpa.tolist(), y=ypa.tolist(), z=zpa.tolist(),
                mode="markers", name="|E| field",
                marker=dict(
                    size=3, color=cpa.tolist(), colorscale="Hot", opacity=0.5,
                    colorbar=dict(title="|E| (V/m)", x=1.02),
                    cmin=float(np.percentile(cpa, 20)), cmax=float(cpa.max()),
                ),
            ))

        # PCB layers as Mesh3d quads
        def _layer(z_mm, color, name, op=0.30):
            x0, x1, y0, y1 = 0.0, BW*1e3, 0.0, BH*1e3
            return go.Mesh3d(x=[x0,x1,x1,x0], y=[y0,y0,y1,y1], z=[z_mm]*4,
                             i=[0,0], j=[1,2], k=[2,3],
                             color=color, opacity=op, name=name, showscale=False)
        # Z reference: B.Cu=0, FR4 from 0→BT, F.Cu from BT→BT+TT, trace top=BT+TT
        traces.append(_layer(Z_BCU*1e3,  "#1565c0", "B.Cu (GND)", op=0.55))   # ground plane — solid
        traces.append(_layer(Z_FCU*1e3,  "#00e5ff", "F.Cu",       op=0.30))   # top copper

        # FR4 substrate box (translucent green)
        x0,x1,y0,y1 = 0,BW*1e3,0,BH*1e3
        z0,z1 = Z_BCU*1e3, Z_FCU*1e3
        traces.append(go.Mesh3d(
            x=[x0,x1,x1,x0,x0,x1,x1,x0], y=[y0,y0,y1,y1,y0,y0,y1,y1],
            z=[z0,z0,z0,z0,z1,z1,z1,z1],
            i=[0,0,0,4,4,4,1,2,0,1,5,6], j=[1,2,4,5,6,7,2,3,3,5,6,7],
            k=[2,3,5,6,7,3,5,6,7,6,2,3],
            color="#a5d6a7", opacity=0.12, name="FR4", showscale=False,
        ))

        # Copper trace rectangle on top of F.Cu
        TW_mm = TW_m * 1e3
        ty0, ty1 = (BH*1e3 - TW_mm)/2, (BH*1e3 + TW_mm)/2
        traces.append(go.Mesh3d(
            x=[0,BW*1e3,BW*1e3,0], y=[ty0,ty0,ty1,ty1], z=[Z_TTOP*1e3]*4,
            i=[0,0], j=[1,2], k=[2,3],
            color="#ffd600", opacity=0.80, name="Cu trace", showscale=False,
        ))

        # Port markers
        for px_mm, label in [(px1*1e3,"Port 1"), (px2*1e3,"Port 2")]:
            traces.append(go.Scatter3d(
                x=[px_mm], y=[py*1e3], z=[port_z*1e3],
                mode="markers+text", name=label, text=[label],
                textposition="top center",
                marker=dict(size=8, color="lime", symbol="diamond"),
            ))

        fig_html = go.Figure(data=traces)
        fig_html.update_layout(
            title=f"|E| interactive 3D — {mid_f/1e9:.2f} GHz  "
                  f"(B.Cu=0 mm, F.Cu={Z_FCU*1e3:.1f} mm, trace top={Z_TTOP*1e3:.2f} mm)",
            scene=dict(
                xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)",
                aspectmode="manual",
                aspectratio=dict(x=BW/BH*1.5, y=1.0, z=0.8),
                camera=dict(eye=dict(x=1.4, y=-1.6, z=1.1)),
            ),
            margin=dict(l=0, r=0, t=40, b=0),
            legend=dict(x=0.01, y=0.99),
        )
        html_path = _OUTDIR / "E_field_3D_interactive.html"
        fig_html.write_html(str(html_path), include_plotlyjs="cdn")
        log(f"  E-field interactive 3D: {html_path}")
    except ImportError:
        log("  Plotly not installed — pip install plotly")
    except Exception as _pe:
        log(f"  Interactive 3D skipped: {_pe}")

    # ── E-field cutplane HTML (EMerge-native API, matches example style) ──────
    # Uses MWField.cutplane() → EHField.scalar('normE','abs') → FieldPlotData.xyzf
    # Three surface cuts (XY, XZ, YZ) rendered as Plotly Surface traces with the
    # PCB geometry overlaid — directly mirrors the example's display.add_surf() call.
    try:
        import plotly.graph_objects as go

        ds_cp = 0.3e-3   # cut-plane discretisation step (0.3 mm)

        def _cutplane_surface(field_dataset, ds, plane, coord, name, colorscale="Hot"):
            """Return a go.Surface trace from an EMerge cutplane, units converted to mm."""
            kwargs = {plane: coord}
            eh_cp = field_dataset.cutplane(ds, **kwargs)
            pd    = eh_cp.scalar("normE", "abs")
            X, Y, Z, F = pd.xyzf
            F = np.nan_to_num(np.abs(F))
            return go.Surface(
                x=X*1e3, y=Y*1e3, z=Z*1e3,
                surfacecolor=F,
                colorscale=colorscale, opacity=0.75,
                showscale=(plane == "z"),          # show colour bar once
                colorbar=dict(title="|E| (V/m)", x=1.02) if plane == "z" else None,
                name=name,
                showlegend=True,
            )

        cp_traces = []

        # XY cut at mid-FR4 (like example's z=0)
        cp_traces.append(_cutplane_surface(
            fld_mid, ds_cp, "z", BT / 2, f"XY  z={BT/2*1e3:.2f} mm (mid-FR4)"))

        # XZ cut at board Y centre
        cp_traces.append(_cutplane_surface(
            fld_mid, ds_cp, "y", BH / 2, f"XZ  y={BH/2*1e3:.1f} mm (centre)",
            colorscale="Plasma"))

        # YZ cut at board X centre (transverse cross-section)
        cp_traces.append(_cutplane_surface(
            fld_mid, ds_cp, "x", BW / 2, f"YZ  x={BW/2*1e3:.1f} mm (centre)",
            colorscale="Viridis"))

        # PCB geometry overlay (same as 3D interactive plot)
        def _pcb_layer(z_mm, color, lname, op=0.4):
            x0, x1, y0, y1 = 0.0, BW*1e3, 0.0, BH*1e3
            return go.Mesh3d(x=[x0,x1,x1,x0], y=[y0,y0,y1,y1], z=[z_mm]*4,
                             i=[0,0], j=[1,2], k=[2,3],
                             color=color, opacity=op, name=lname, showscale=False)

        cp_traces.append(_pcb_layer(Z_BCU*1e3,  "#1565c0", "B.Cu (GND)", 0.5))
        cp_traces.append(_pcb_layer(Z_FCU*1e3,  "#00e5ff", "F.Cu",       0.25))
        TW_mm2 = TW_m * 1e3
        ty0c, ty1c = (BH*1e3 - TW_mm2)/2, (BH*1e3 + TW_mm2)/2
        cp_traces.append(go.Mesh3d(
            x=[0,BW*1e3,BW*1e3,0], y=[ty0c,ty0c,ty1c,ty1c], z=[Z_TTOP*1e3]*4,
            i=[0,0], j=[1,2], k=[2,3],
            color="#ffd600", opacity=0.85, name="Cu trace", showscale=False,
        ))

        fig_cp = go.Figure(data=cp_traces)
        fig_cp.update_layout(
            title=(f"|E| cut-plane view — {mid_f/1e9:.2f} GHz  "
                   f"({'ABC' if _USE_ABC else 'no ABC'})"),
            scene=dict(
                xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)",
                aspectmode="manual",
                aspectratio=dict(x=BW/BH*1.5, y=1.0, z=0.8),
                camera=dict(eye=dict(x=1.5, y=-1.8, z=1.2)),
            ),
            margin=dict(l=0, r=0, t=45, b=0),
            legend=dict(x=0.01, y=0.99),
        )
        cp_path = _OUTDIR / "E_field_cutplane.html"
        fig_cp.write_html(str(cp_path), include_plotlyjs="cdn")
        log(f"  E-field cutplane HTML: {cp_path}")

    except ImportError:
        log("  Plotly not installed — pip install plotly")
    except Exception as _cpe:
        log(f"  Cutplane HTML skipped: {_cpe}")

    # ── S-param plot ──────────────────────────────────────────────────────────
    fig2, ax2 = plt.subplots(figsize=(8, 4))
    freqs_ghz = np.array(freq_axis) / 1e9
    ax2.plot(freqs_ghz, 20*np.log10(np.abs(Smat[:,0,0])+1e-30), label="S11")
    ax2.plot(freqs_ghz, 20*np.log10(np.abs(Smat[:,1,0])+1e-30), label="S21")
    ax2.set_xlabel("Frequency (GHz)"); ax2.set_ylabel("Magnitude (dB)")
    ax2.set_title("Microstrip S-parameters"); ax2.legend(); ax2.grid(True)
    sp_path = _OUTDIR / "S_params.png"
    fig2.savefig(str(sp_path), dpi=150, bbox_inches="tight"); plt.close(fig2)
    log(f"  S-param plot: {sp_path}")

    # ── Z0 plot ───────────────────────────────────────────────────────────────
    fig3, axes = plt.subplots(1, 2, figsize=(12, 4))

    ax_z = axes[0]
    ax_z.plot(freqs_ghz, Z0_extracted.real, label="Re(Z0) sim", lw=2)
    ax_z.plot(freqs_ghz, Z0_extracted.imag, label="Im(Z0) sim", lw=2, ls="--")
    ax_z.plot(freqs_ghz, np.abs(Z0_extracted), label="|Z0| sim",  lw=2, ls=":")
    ax_z.axhline(Z0_analytical, color="red",  lw=1.5, ls="-.",
                 label=f"Analytical {Z0_analytical:.1f} Ω")
    ax_z.axhline(50.0, color="gray", lw=1, ls=":", label="50 Ω ref")
    ax_z.set_xlabel("Frequency (GHz)"); ax_z.set_ylabel("Impedance (Ω)")
    ax_z.set_title("Characteristic Impedance Z0  (from S11)")
    ax_z.legend(fontsize=8); ax_z.grid(True)

    ax_b = axes[1]
    k0_arr = 2 * np.pi * np.array(freq_axis) / 3e8
    beta_analytical = k0_arr * np.sqrt(_eeff)
    ax_b.plot(freqs_ghz, beta_extracted, color="purple", lw=2,
              label="β sim (from ∠S21)")
    ax_b.plot(freqs_ghz, beta_analytical, color="orange", lw=1.5, ls="--",
              label=f"β analytical (εeff={_eeff:.3f})")
    # Secondary axis: εeff
    ax_b2 = ax_b.twinx()
    mask_b = beta_extracted > 0
    ax_b2.plot(np.array(freqs_ghz)[mask_b], eeff_extracted[mask_b],
               color="green", lw=1.5, ls=":", label="εeff sim")
    ax_b2.axhline(_eeff, color="olive", lw=1, ls=":", label=f"εeff analytical={_eeff:.3f}")
    ax_b2.set_ylabel("εeff", color="green")
    ax_b.set_xlabel("Frequency (GHz)"); ax_b.set_ylabel("β (rad/m)")
    ax_b.set_title("Phase Constant β  &  εeff")
    ax_b.legend(fontsize=8, loc="upper left"); ax_b2.legend(fontsize=8, loc="lower right")
    ax_b.grid(True)

    fig3.suptitle(f"Microstrip  W={TW_m*1e3:.1f} mm  h={BT*1e3:.1f} mm  εr={ER}",
                  fontsize=10)
    fig3.tight_layout()
    z0_path = _OUTDIR / "Z0_impedance.png"
    fig3.savefig(str(z0_path), dpi=150, bbox_inches="tight"); plt.close(fig3)
    log(f"  Z0 plot: {z0_path}")

except Exception as e:
    import traceback
    log(f"Plot error: {e}")
    log(traceback.format_exc())

log(f"\nDone.  Total: {time.monotonic()-t0:.1f} s")
log(f"Output: {_OUTDIR}")
