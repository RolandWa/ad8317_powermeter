"""Round-trip and error tests for s3p_io (no pytest needed): python test_s3p_io.py"""
import tempfile
from pathlib import Path

import numpy as np

from s3p_io import n_ports_from_name, read_touchstone, write_touchstone


def rnd(nf, n, seed):
    rng = np.random.default_rng(seed)
    return 0.3 * (rng.standard_normal((nf, n, n)) + 1j * rng.standard_normal((nf, n, n)))


def test_roundtrip():
    f = np.array([1e6, 2.5e9, 1e10])
    with tempfile.TemporaryDirectory() as d:
        for n in (1, 2, 3, 4):
            S = rnd(3, n, n)
            p = Path(d) / ("x.s%dp" % n)
            write_touchstone(p, f, S, ["test"], z0=75.0)
            f2, S2, z0 = read_touchstone(p)
            assert z0 == 75.0 and np.allclose(f2, f) and np.allclose(S2, S, atol=1e-8), n
    print("round trip n=1..4 OK")


def test_formats_and_units():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.s1p"
        p.write_text("! c\n# MHz S MA R 50\n100 0.5 90\n200 0.5 -90 ! tail\n")
        f, S, z0 = read_touchstone(p)
        assert np.allclose(f, [1e8, 2e8]) and np.allclose(S[:, 0, 0], [0.5j, -0.5j])
        p.write_text("# GHz S DB R 50\n1 -6.0206 0\n")
        assert abs(read_touchstone(p)[1][0, 0, 0] - 0.5) < 1e-4
        p.write_text("# HZ S RI\n1 0.1 0.2\n")           # no R: default 50
        assert read_touchstone(p)[2] == 50.0
    print("MA / DB / units / default R OK")


def test_errors():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v2.s2p"
        p.write_text("[Version] 2.0\n# HZ S RI R 50\n[Number of Ports] 2\n")
        for bad, txt in (("v2.s2p", "[Version] 2.0\n# HZ S RI R 50\n1 0 0 0 0 0 0 0 0\n"),
                         ("y.s1p", "# HZ Y RI R 50\n1 0 0\n"),
                         ("short.s2p", "# HZ S RI R 50\n1 0 0 0 0\n"),
                         ("r.s1p", "# HZ S RI R\n1 0 0\n")):
            q = Path(d) / bad
            q.write_text(txt)
            try:
                read_touchstone(q)
            except ValueError:
                continue
            raise AssertionError("no error for " + bad)
    try:
        n_ports_from_name("data.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("name check")
    assert n_ports_from_name("A.S12P") == 12
    print("errors OK")


if __name__ == "__main__":
    test_roundtrip()
    test_formats_and_units()
    test_errors()
    print("PASS")
