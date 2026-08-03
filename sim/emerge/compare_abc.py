"""
Compare S-parameter and Z0 results with and without absorbing boundary conditions.

Reads:
  results/simple_test/microstrip_test_with_abc.s2p
  results/simple_test/microstrip_test_no_abc.s2p

Outputs:
  results/simple_test/comparison_S_params.png
  results/simple_test/comparison_Z0.png
"""

import sys, pathlib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTDIR = pathlib.Path(__file__).parent / "results" / "simple_test"

# ── Geometry constants (must match test_simple_microstrip.py) ─────────────────
BW  = 10e-3; BT = 1.6e-3; TT = 35e-6; ER = 4.4
TW_m = 3e-3
px1  = 1e-3
_Wu  = TW_m + (TT / np.pi) * (1 + np.log(2 * BT / TT))
_ue  = _Wu / BT
_eeff = (ER + 1)/2 + (ER - 1)/2 * (1 + 12/_ue)**-0.5
Z0_analytical = (120 * np.pi / np.sqrt(_eeff)) / (_ue + 1.393 + 0.667 * np.log(_ue + 1.444))
_Lline = max(BW - 2 * px1, BW * 0.8)


def read_s2p(path):
    """Return (freq_Hz, S_complex[M,2,2]) from a .s2p Touchstone file."""
    freqs, rows = [], []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("!"):
                continue
            if line.startswith("#"):
                parts = line.split()
                fmul = {"HZ": 1, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}.get(
                    parts[1].upper(), 1.0)
                fmt = parts[3].upper()   # RI / MA / DB
                continue
            vals = list(map(float, line.split()))
            freqs.append(vals[0] * fmul)
            rows.append(vals[1:])
    M = len(freqs)
    freq = np.array(freqs)
    S = np.zeros((M, 2, 2), dtype=complex)
    for k, r in enumerate(rows):
        # S2P order: S11 S21 S12 S22  (each as pair)
        pairs = [(r[2*i], r[2*i+1]) for i in range(4)]
        order = [(0,0),(1,0),(0,1),(1,1)]
        for (i,j),(a,b) in zip(order, pairs):
            if fmt == "RI":
                S[k,i,j] = complex(a, b)
            elif fmt == "MA":
                S[k,i,j] = a * np.exp(1j * np.radians(b))
            elif fmt == "DB":
                S[k,i,j] = 10**(a/20) * np.exp(1j * np.radians(b))
    return freq, S


def extract_z0(freq, S):
    Z0_ext = np.zeros(len(freq), dtype=complex)
    eeff   = np.zeros(len(freq))
    for k, f in enumerate(freq):
        S11 = S[k,0,0]; S21 = S[k,1,0]
        d = 1 - S11
        if abs(d) > 1e-10:
            Z0_ext[k] = 50.0 * (1 + S11) / d
        ph = -np.angle(S21)
        if f > 0 and ph != 0:
            k0 = 2*np.pi*f/3e8
            b  = ph / _Lline
            eeff[k] = (b/k0)**2 if k0 > 0 else 0.0
    return Z0_ext, eeff


# ── Load files ────────────────────────────────────────────────────────────────
files = {
    "With ABC":    OUTDIR / "microstrip_test_with_abc.s2p",
    "Without ABC": OUTDIR / "microstrip_test_no_abc.s2p",
}

data = {}
for label, path in files.items():
    if not path.exists():
        print(f"MISSING: {path}"); sys.exit(1)
    freq, S = read_s2p(path)
    Z0, eeff = extract_z0(freq, S)
    data[label] = dict(freq=freq, S=S, Z0=Z0, eeff=eeff)
    print(f"Loaded {label}: {len(freq)} freq points")

colors = {"With ABC": "#2196f3", "Without ABC": "#f44336"}

# ── Plot 1: S-parameters ──────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle("S-parameters: With vs Without ABC  (Absorbing Boundary)", fontsize=13)

for label, d in data.items():
    f = d["freq"] / 1e9
    S = d["S"]
    s11 = 20*np.log10(np.abs(S[:,0,0]) + 1e-30)
    s21 = 20*np.log10(np.abs(S[:,1,0]) + 1e-30)
    axes[0].plot(f, s11, "o-", color=colors[label], label=label, lw=2, ms=6)
    axes[1].plot(f, s21, "o-", color=colors[label], label=label, lw=2, ms=6)

axes[0].set_title("|S11| — Return loss")
axes[0].set_xlabel("Frequency (GHz)"); axes[0].set_ylabel("dB")
axes[0].legend(); axes[0].grid(True, alpha=0.4)
axes[0].axhline(-10, color="gray", ls=":", lw=1, label="-10 dB")

axes[1].set_title("|S21| — Insertion loss")
axes[1].set_xlabel("Frequency (GHz)"); axes[1].set_ylabel("dB")
axes[1].legend(); axes[1].grid(True, alpha=0.4)

plt.tight_layout()
out1 = OUTDIR / "comparison_S_params.png"
plt.savefig(out1, dpi=150)
plt.close()
print(f"S-param comparison: {out1}")

# ── Plot 2: Z0 and εeff ───────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
fig.suptitle("Impedance & Effective Permittivity: With vs Without ABC", fontsize=13)

for label, d in data.items():
    f = d["freq"] / 1e9
    axes[0].plot(f, np.abs(d["Z0"]), "o-", color=colors[label], label=label, lw=2, ms=6)
    axes[1].plot(f, d["eeff"],        "o-", color=colors[label], label=label, lw=2, ms=6)

axes[0].axhline(Z0_analytical, color="green", ls="--", lw=1.5,
                label=f"Analytical Z0 = {Z0_analytical:.1f} Ω")
axes[0].set_title("|Z0| extracted from S11")
axes[0].set_xlabel("Frequency (GHz)"); axes[0].set_ylabel("Ω")
axes[0].legend(); axes[0].grid(True, alpha=0.4)

axes[1].axhline(_eeff, color="green", ls="--", lw=1.5,
                label=f"Analytical εeff = {_eeff:.3f}")
axes[1].set_title("Effective Permittivity εeff from S21 phase")
axes[1].set_xlabel("Frequency (GHz)"); axes[1].set_ylabel("εeff")
axes[1].legend(); axes[1].grid(True, alpha=0.4)

plt.tight_layout()
out2 = OUTDIR / "comparison_Z0.png"
plt.savefig(out2, dpi=150)
plt.close()
print(f"Z0 comparison:      {out2}")

# ── Console table ─────────────────────────────────────────────────────────────
print()
print(f"{'Freq(GHz)':<10} {'S11 no_abc':>12} {'S11 abc':>12} {'ΔS11':>8} "
      f"{'S21 no_abc':>12} {'S21 abc':>12} {'ΔS21':>8}")
print("-" * 80)
d_abc  = data["With ABC"]
d_none = data["Without ABC"]
for k in range(len(d_abc["freq"])):
    f  = d_abc["freq"][k]/1e9
    s11a = 20*np.log10(abs(d_abc ["S"][k,0,0])+1e-30)
    s11n = 20*np.log10(abs(d_none["S"][k,0,0])+1e-30)
    s21a = 20*np.log10(abs(d_abc ["S"][k,1,0])+1e-30)
    s21n = 20*np.log10(abs(d_none["S"][k,1,0])+1e-30)
    print(f"{f:<10.3f} {s11n:>12.2f} {s11a:>12.2f} {s11a-s11n:>+8.2f} "
          f"{s21n:>12.2f} {s21a:>12.2f} {s21a-s21n:>+8.2f}")

print()
print(f"  Analytical  Z0 = {Z0_analytical:.1f} Ω   εeff = {_eeff:.3f}")
print()
print(f"{'Freq(GHz)':<10} {'|Z0| no_abc':>13} {'|Z0| abc':>13} {'Δ|Z0|':>9} "
      f"{'eeff no_abc':>13} {'eeff abc':>13}")
print("-" * 76)
for k in range(len(d_abc["freq"])):
    f  = d_abc["freq"][k]/1e9
    z0a = abs(d_abc ["Z0"][k]); z0n = abs(d_none["Z0"][k])
    ea  = d_abc ["eeff"][k];    en  = d_none["eeff"][k]
    print(f"{f:<10.3f} {z0n:>13.2f} {z0a:>13.2f} {z0a-z0n:>+9.2f} "
          f"{en:>13.4f} {ea:>13.4f}")

print("\nDone.")
