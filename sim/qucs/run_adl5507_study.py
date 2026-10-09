"""S11 of the J1 -> RF detector input, ADL5507 against AD8317, 1 MHz - 10 GHz (qucsator).

The ADL5507 has ONE RF input pin to ground (RFIN, ac-coupled inside), so its model is a 1-port
(adl5507_input_model.py); the AD8317 is the INHI/INLO 2-port of ad8317_input_model.py. All board
cases use the substrate-defined lines of the AD8317 study (run_s11_study.chain_C: SUBST + CLIN/CSTEP
or MLIN/MSTEP) with the real geometry of J1 -> U1, so the two chips see the same launch.

Cases (E = ADL5507, C = AD8317 from run_s11_study, same stackup letters):
  E0r  ADL5507 bare RFIN, directly at a 50 ohm port         (chip alone, datasheet Table 5)
  E0s  ADL5507 with the 51 ohm shunt of the datasheet        (chip alone, datasheet Table 4)
  E0i  ADL5507 bare RFIN in parallel with an ideal 51 ohm    (chip alone, ideal resistor)
  A0r  AD8317 INHI to ground (INLO ac-grounded), bare        (chip alone)
  A0t  AD8317 INHI with the 52.3 ohm termination of its datasheet test circuit
  Board cases, for each of the 4 stackups of the AD8317 study (cpw/bcu = as built, ms/bcu,
  cpw/in1, ms/in1):
  E1   "drop-in": the AD8317 board as it is (R1 = R2 = 100 ohm shunts, C1 47 nF, L4) with the
       ADL5507 at the place of the AD8317, INLO side not used
  E2   ADI reference: the same lines, no R1/R2/C1, one 51 ohm shunt (0.25 nH ESL) at RFIN
  E3   the same lines, the chip from datasheet Table 4 (51 ohm included, as measured by ADI)
  C    the AD8317 (INHI-INLO 2-port) on the same lines (run_s11_study case C1..C4)

Outputs: case_E*.net / .dat, adl5507_vs_ad8317.csv, adl5507_vs_ad8317_*.png, summary on stdout.

    python run_adl5507_study.py
"""
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_s11_study as st  # noqa: E402
import adl5507_input_model as adl  # noqa: E402
import ad8317_input_model as ad  # noqa: E402

RAW_FILE = adl.file_names("raw")
SHUNT_FILE = adl.file_names("shunt51")
AD_S1P = ad.file_names("table")[0]
STACKUPS = [("cpw", "bcu"), ("ms", "bcu"), ("cpw", "in1"), ("ms", "in1")]
STACKUP_NAME = {("cpw", "bcu"): "coplanar, B.Cu 1.44 mm (as built)", ("ms", "bcu"): "microstrip, B.Cu 1.44 mm",
                ("cpw", "in1"): "coplanar, In1 0.1 mm (what if)", ("ms", "in1"): "microstrip, In1 0.1 mm (what if)"}
BANDS = [(1e6, 1e8), (1e8, 1e9), (1e9, 3e9), (3e9, 6e9), (6e9, 1e10)]
SPOTS = [0.1e9, 0.9e9, 1.9e9, 2.4e9, 3.6e9, 5.8e9, 8e9, 10e9]


def netlist_chip_alone(title, chip_file, shunt_ohm=None):
    s = "# " + title + st.NL + st.PORT
    s += 'SPfile:CHIP n1 gnd File="%s" Data="rectangular" Interpolator="linear" duringDC="open"' % chip_file + st.NL
    if shunt_ohm:
        s += 'R:RT n1 gnd R="%g Ohm"' % shunt_ohm + st.NL
    return s + st.SWEEP


def chain_E(line, plane, mode, geom=None):
    """The J1 -> RFIN chain of the ADL5507. Returns (netlist, elements, taps).

    mode: "dropin" (board as it is), "adi" (51 ohm at the pin, bare chip), "table" (Table 4 chip).
    """
    G = st.merged_geom(geom)
    w, l, g = G["w"], G["l"], G["g"]
    el = []
    st.mk_lin(el, line, "PAD", "n1", "a", G["pad"][0], G["pad"][1], G["pad"][2])
    if line == "ms":
        st.mk_step(el, line, "S0", "a", "a2", G["pad"][0], w["L1"], g)
        st.mk_lin(el, line, "L1", "a2", "r1a", w["L1"], g, l["L1"])
    else:
        st.mk_lin(el, line, "L1", "a", "r1a", w["L1"], g, l["L1"])
    st.mk_step(el, line, "S1", "r1a", "r1", w["L1"], w["L2"], g)
    st.mk_lin(el, line, "L2", "r1", "r2a", w["L2"], g, l["L2"])
    st.mk_step(el, line, "S2", "r2a", "r2", w["L2"], w["L3"], g)
    if mode == "dropin":
        st.mk_lin(el, line, "L3", "r2", "c1a", w["L3"], g, l["L3"])
        st.mk_part(el, "C", "C1", "c1a", "c1m", "47 nF")
        st.mk_part(el, "L", "C1L", "c1m", "c1n", "0.25 nH")
        st.mk_part(el, "R", "C1R", "c1n", "c1b", "0.035 Ohm")
        st.mk_lin(el, line, "L4", "c1b", "inhi", w["L4"], g, l["L4"])
        taps = [("R1", "r1", G["r_shunt"]), ("R2", "r2", G["r_shunt"])]
        chip = RAW_FILE
    else:
        st.mk_lin(el, line, "L3", "r2", "l3b", w["L3"], g, l["L3"])
        st.mk_step(el, line, "S3", "l3b", "l4a", w["L3"], w["L4"], g)
        st.mk_lin(el, line, "L4", "l4a", "inhi", w["L4"], g, l["L4"])
        taps = [("RT", "inhi", adl.R_SHUNT)] if mode == "adi" else []
        chip = RAW_FILE if mode == "adi" else SHUNT_FILE
    el.append(dict(kind="SPfile", name="CHIP", a="inhi", props=[("File", chip)]))
    title = "E (%s): ADL5507 on %s, %s" % (mode, line, plane)
    return st.netlist_from_elements(title, el, taps, plane), el, taps


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


def band_table(f, res, keys):
    lines = ["%-46s" % "worst |S11| [dB] in band" + "".join("%12s" % ("%g-%g" % (a / 1e9, b / 1e9)) for a, b in BANDS)]
    for k in keys:
        row = "%-46s" % k[:46]
        for a, b in BANDS:
            m = (f >= a) & (f <= b)
            row += "%12.1f" % db(res[k][m]).max()
        lines.append(row)
    return "\n".join(lines)


def spot_table(f, res, keys):
    lines = ["%-46s" % "return loss RL = -|S11| [dB] at" + "".join("%8s" % ("%gG" % (s / 1e9)) for s in SPOTS)]
    for k in keys:
        row = "%-46s" % k[:46]
        for s in SPOTS:
            row += "%8.1f" % (-db(res[k][np.argmin(abs(f - s))]))
        lines.append(row)
    return "\n".join(lines)


def main():
    adl.main()                                   # writes the s1p files
    ad.main()
    res, f = {}, None

    def go(label, name, text):
        nonlocal f
        f, res[label] = st.run(name, text)

    go("A0r AD8317 INHI bare", "case_E_A0r_ad8317_bare", netlist_chip_alone("AD8317 INHI to ground", AD_S1P))
    go("A0t AD8317 + 52.3 ohm", "case_E_A0t_ad8317_52R", netlist_chip_alone("AD8317 INHI + 52.3 ohm", AD_S1P, 52.3))
    go("E0r ADL5507 bare (Table 5)", "case_E0r_adl5507_bare", netlist_chip_alone("ADL5507 raw", RAW_FILE))
    go("E0s ADL5507 + 51 ohm (Table 4)", "case_E0s_adl5507_table4", netlist_chip_alone("ADL5507 Table 4", SHUNT_FILE))
    go("E0i ADL5507 bare + ideal 51 ohm", "case_E0i_adl5507_51R", netlist_chip_alone("ADL5507 raw + 51 ohm", RAW_FILE, 51.0))
    for line, plane in STACKUPS:
        tag = "%s_%s" % (line, plane)
        sn = STACKUP_NAME[(line, plane)]
        go("C  AD8317 on %s" % sn, "case_E_C_%s_ad8317" % tag, st.netlist_C(line, plane))
        for mode, key, label in (("dropin", "E1", "E1 ADL5507 drop-in (R1,R2,C1)"),
                                 ("adi", "E2", "E2 ADL5507 + 51 ohm at pin"),
                                 ("table", "E3", "E3 ADL5507 Table 4 chip")):
            go("%s on %s" % (label, sn), "case_%s_%s_adl5507_%s" % (key, tag, mode), chain_E(line, plane, mode)[0])

    with open(HERE / "adl5507_vs_ad8317.csv", "w", newline="\n") as fh:
        fh.write("freq_Hz," + ",".join('"%s"' % k + "_dB" for k in res) + "\n")
        for i, fk in enumerate(f):
            fh.write("%.6e," % fk + ",".join("%.3f" % db(v[i]) for v in res.values()) + "\n")

    # figure 1: the chips alone
    chips = [k for k in res if k[:3] in ("A0r", "A0t", "E0r", "E0s", "E0i")]
    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    for k in chips:
        ax.semilogx(f / 1e6, db(res[k]), label=k, ls="--" if k.startswith("A0") else "-")
    ax.axhline(-10, color="k", lw=.6, ls=":")
    ax.axhline(-15, color="k", lw=.6, ls=":")
    ax.set_ylim(-40, 2)
    ax.set_xlabel("MHz")
    ax.set_ylabel("|S11| [dB]")
    ax.grid(True, which="both", alpha=.3)
    ax.legend(fontsize=8, loc="lower left")
    ax.set_title("RF input of the detector alone (50 ohm reference): AD8317 against ADL5507")
    fig.tight_layout()
    fig.savefig(HERE / "adl5507_vs_ad8317_chips.png", dpi=130)

    # figure 2: J1 -> chip on the four stackups
    fig, axs = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True)
    for a, (line, plane) in zip(axs.ravel(), STACKUPS):
        sn = STACKUP_NAME[(line, plane)]
        for k, v in res.items():
            if k.endswith(" on " + sn):
                a.semilogx(f / 1e6, db(v), label=k.replace(" on " + sn, ""))
        a.axhline(-10, color="k", lw=.6, ls=":")
        a.axhline(-15, color="k", lw=.6, ls=":")
        a.set_title(sn, fontsize=10)
        a.set_ylim(-40, 2)
        a.grid(True, which="both", alpha=.3)
        a.legend(fontsize=7, loc="lower left")
    for a in axs[1]:
        a.set_xlabel("MHz")
    for a in axs[:, 0]:
        a.set_ylabel("|S11| [dB]")
    fig.suptitle("S11 at J1: AD8317 (C) against ADL5507 (E1 drop-in, E2 ADI 51 ohm, E3 Table 4), same lines")
    fig.tight_layout()
    fig.savefig(HERE / "adl5507_vs_ad8317_board.png", dpi=130)

    asb = STACKUP_NAME[("cpw", "bcu")]
    keys = chips + [k for k in res if k.endswith(" on " + asb)]
    print("=== chips alone and the board as built (%s) ===" % asb)
    print(band_table(f, res, keys))
    print()
    print(spot_table(f, res, keys))
    print()
    # fraction of the band with RL >= 10 / 15 dB, on the as-built stackup
    print("share of 1 MHz - 10 GHz (log points) with RL >= 10 dB / >= 15 dB:")
    for k in keys:
        s = db(res[k])
        print("  %-52s %5.1f %%  %5.1f %%" % (k, 100 * np.mean(s <= -10), 100 * np.mean(s <= -15)))
    print("\nmean mismatch loss -10log10(1-|G|^2) [dB] over 10 MHz - 6 GHz:")
    m = (f >= 1e7) & (f <= 6e9)
    for k in keys:
        print("  %-52s %6.2f" % (k, np.mean(-10 * np.log10(1 - np.abs(res[k][m]) ** 2))))


if __name__ == "__main__":
    main()
