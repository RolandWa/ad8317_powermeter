"""Touchstone v1 helpers (numpy only): read .sNp, write wrapped .sNp.

Not supported (a clear error is raised): Touchstone 2.0 files ([Version] 2.0,
[Number of Ports] ...), which also covers mixed-mode data.
"""
import re
from pathlib import Path

import numpy as np

_UNIT = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def n_ports_from_name(path):
    """Give the number of ports from the extension: `x.s3p` -> 3."""
    m = re.search(r"\.s(\d+)p$", str(path), re.I)
    if not m:
        raise ValueError("%s: not a Touchstone file name (.sNp)" % path)
    return int(m.group(1))


def _parse_options(line):
    """Parse '# <unit> <param> <format> R <z0>' (any order after the unit
    tokens, every part optional). Returns (unit multiplier, param, fmt, z0)."""
    tok = line[1:].lower().split()
    mult, param, fmt, z0 = 1e9, "s", "ma", 50.0
    i = 0
    while i < len(tok):
        t = tok[i]
        if t in _UNIT:
            mult = _UNIT[t]
        elif t in ("s", "y", "z", "h", "g"):
            param = t
        elif t in ("ri", "ma", "db"):
            fmt = t
        elif t == "r":
            if i + 1 >= len(tok):
                raise ValueError("option line has 'R' with no value: %r" % line)
            z0 = float(tok[i + 1])
            i += 1
        else:
            raise ValueError("unknown token %r in option line %r" % (t, line))
        i += 1
    return mult, param, fmt, z0


def read_touchstone(path):
    """Return f [Hz], S [nf, n, n], z0. Only S-parameters are accepted."""
    n = n_ports_from_name(path)
    mult, param, fmt, z0 = 1e9, "s", "ma", 50.0
    vals = []
    seen_options = False
    for line in open(path, encoding="ascii", errors="ignore"):
        line = line.split("!")[0].strip()
        if not line:
            continue
        if line.startswith("["):
            raise ValueError("%s: Touchstone 2.0 keyword %r is not supported"
                             % (path, line.split("]")[0] + "]"))
        if line.startswith("#"):
            if not seen_options:  # only the first option line counts
                mult, param, fmt, z0 = _parse_options(line)
                seen_options = True
            continue
        vals += [float(x) for x in line.split()]
    if param != "s":
        raise ValueError("%s: parameter type %r is not S" % (path, param))
    width = 1 + 2 * n * n
    if len(vals) == 0 or len(vals) % width:
        raise ValueError("%s: %d numbers do not fill whole rows of %d for %d ports"
                         % (path, len(vals), width, n))
    a = np.array(vals).reshape(-1, width)
    f = a[:, 0] * mult
    p, q = a[:, 1::2], a[:, 2::2]
    if fmt == "ri":
        c = p + 1j * q
    elif fmt == "ma":
        c = p * np.exp(1j * np.deg2rad(q))
    else:
        c = 10 ** (p / 20) * np.exp(1j * np.deg2rad(q))
    S = c.reshape(-1, n, n)
    if n == 2:  # a 2-port file lists S11 S21 S12 S22
        S = S.reshape(-1, 2, 2).transpose(0, 2, 1)
    return f, S, z0


def write_touchstone(path, f, S, comments=(), z0=50.0):
    """Write a Touchstone v1 file in Hz / RI. 3 or more ports: one row of the
    matrix per line (Qucs and most tools need that wrapping)."""
    S = np.asarray(S)
    n = S.shape[1]
    with Path(path).open("w", encoding="ascii", newline="\n") as fh:
        for c in comments:
            fh.write("! " + c + "\n")
        fh.write("# HZ S RI R %g\n" % z0)
        for k, fk in enumerate(f):
            if n == 1:
                rows = [[S[k, 0, 0]]]
            elif n == 2:
                rows = [[S[k, 0, 0], S[k, 1, 0], S[k, 0, 1], S[k, 1, 1]]]
            else:
                rows = [list(S[k, i, :]) for i in range(n)]
            for i, row in enumerate(rows):
                pre = "%.6e " % fk if i == 0 else "    "
                fh.write(pre + " ".join("%.9e %.9e" % (v.real, v.imag) for v in row) + "\n")
