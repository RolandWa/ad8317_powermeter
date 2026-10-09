"""Check each generated .sch: Qucs must convert it (`qucs -n`) into a netlist
that gives the same S11 as the qucsator netlist of run_s11_study.py.

    python verify_qucs_schematics.py
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_s11_study as st  # noqa: E402
from make_qucs_schematics import CASES  # noqa: E402


def find_qucs():
    cand = os.environ.get("QUCS") or str(Path(st.QUCSATOR).with_name("qucs.exe"))
    if not os.path.isfile(cand):
        raise SystemExit("qucs.exe not found: set the QUCS environment variable")
    return cand


def run(exe, args, timeout=90):
    r = subprocess.run([exe] + args, cwd=HERE, capture_output=True, text=True, timeout=timeout)
    return r


def main():
    qucs = find_qucs()
    ok_all = True
    names = list(CASES) + sorted(q.stem for q in HERE.glob("case_OPT_*.sch"))
    for name in names:
        sch = HERE / (name + ".sch")
        net_sch = HERE / (name + "__from_sch.net")
        if net_sch.exists():
            net_sch.unlink()
        r = run(qucs, ["-n", "-i", str(sch), "-o", str(net_sch)])
        if not net_sch.exists():
            print("%-32s FAIL: qucs -n made no netlist (%s)" % (name, (r.stderr or r.stdout)[-200:]))
            ok_all = False
            continue
        text = net_sch.read_text()
        # nodes that no wire reaches show up as _netN once; a clean schematic has every _net twice or more
        singles = [n for n in set(re.findall(r"\b_net\d+\b", text)) if len(re.findall(r"\b%s\b" % n, text)) < 2]
        f_ref, s_ref = st.run(name + "__ref", (HERE / (name + ".net")).read_text())
        # the netlist from the schematic has the same text for the sweep; run it as it is
        f_sch, s_sch = st.run(name + "__sch", text)
        err = float(np.max(np.abs(s_ref - s_sch)))
        status = "OK  " if err < 1e-6 and not singles else "FAIL"
        ok_all &= status.strip() == "OK"
        print("%-32s %s max|dS11| = %.2e   dangling nets: %s" % (name, status, err, singles or "none"))
        for ext in (".dat",):
            for tag in ("__ref", "__sch"):
                (HERE / (name + tag + ext)).unlink(missing_ok=True)
        for tag in ("__ref", "__sch"):
            (HERE / (name + tag + ".net")).unlink(missing_ok=True)
        net_sch.unlink(missing_ok=True)
    print("PASS" if ok_all else "FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
