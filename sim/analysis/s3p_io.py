"""Touchstone helpers (numpy only)."""
import numpy as np

def read_touchstone(path):
    """Return f[Hz], S[nf,n,n] (RI/MA/DB, any freq unit), z0, n ports from .sNp."""
    unit = {"hz": 1, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}
    fmt, mult, z0, n = "ma", 1e9, 50.0, int(str(path)[-2])
    vals = []
    for line in open(path, encoding="ascii", errors="ignore"):
        line = line.split("!")[0].strip()
        if not line:
            continue
        if line.startswith("#"):
            t = line[1:].lower().split()
            mult = unit[t[0]]; fmt = t[2]; z0 = float(t[4]) if "r" in t else 50.0
            continue
        vals += [float(x) for x in line.split()]
    a = np.array(vals).reshape(-1, 1 + 2 * n * n)
    f = a[:, 0] * mult
    p = a[:, 1::2] ; q = a[:, 2::2]
    if fmt == "ri": c = p + 1j * q
    elif fmt == "ma": c = p * np.exp(1j * np.deg2rad(q))
    else: c = 10 ** (p / 20) * np.exp(1j * np.deg2rad(q))
    S = c.reshape(-1, n, n)
    if n == 2:  # touchstone 2-port order is S11 S21 S12 S22
        S = S.reshape(-1, 2, 2).transpose(0, 2, 1)
    return f, S, z0
