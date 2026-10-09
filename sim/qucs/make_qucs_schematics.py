"""Write Qucs 0.0.19 schematics (.sch) for the J1 -> AD8317 study.

The schematics hold the same circuits as the qucsator netlists of
`run_s11_study.py`; the values are taken from there, so the two cannot drift.
Pin positions (grid units, relative to the component origin):
  R, L, C, TLIN, horizontal: (-30,0) (+30,0); rotated by 1: (0,-30) (0,+30)
  Pac rotated by 1: pin 1 (0,-30), pin 2 (0,+30);  GND: pin at its origin
  SPfile 1 port:  1 (-30,0)  Ref (0,+30)
  SPfile 2 ports: 1 (-30,0)  2 (+30,0)  Ref (0,+30)
  SPfile 3 ports: 1 (-30,-30)  2 (+30,-30)  3 (-30,+30)  Ref (0,+60)
(found with `qucs -n` on a test schematic; see verify_qucs_schematics.py).

The S-parameter files are given with ABSOLUTE paths, because Qucs resolves a
relative name against its own project directory (~/.qucs). Run this script
again after you move the repository.

    python make_qucs_schematics.py
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_s11_study as st  # noqa: E402  (netlist values, file names)

HEADER = """<Qucs Schematic 0.0.19>
<Properties>
  <View=0,0,{vw},{vh},1,0,0>
  <Grid=10,10,1>
  <DataSet={name}.dat>
  <DataDisplay={name}.dpl>
  <OpenDisplay=1>
  <Script={name}.m>
  <RunScript=0>
  <showFrame=0>
  <FrameText0=Title>
  <FrameText1=Drawn By:>
  <FrameText2=Date:>
  <FrameText3=Revision:>
</Properties>
<Symbol>
</Symbol>
"""


class Sch:
    def __init__(self, name):
        self.name, self.comp, self.wires = name, [], []
        self.n = {}

    def _id(self, kind):
        self.n[kind] = self.n.get(kind, 0) + 1
        return self.n[kind]

    def add(self, kind, name, x, y, tx, ty, rot, props, mirror=0):
        """props: list of (value, visible)."""
        p = " ".join('"%s" %d' % (v, vis) for v, vis in props)
        self.comp.append("  <%s %s 1 %d %d %d %d %d %d %s>" % (kind, name, x, y, tx, ty, mirror, rot, p))

    def wire(self, x1, y1, x2, y2):
        if (x1, y1) != (x2, y2):
            self.wires.append('  <%d %d %d %d "" 0 0 0 "">' % (x1, y1, x2, y2))

    def path(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.wire(a[0], a[1], b[0], b[1])

    def gnd(self, x, y):
        self.comp.append("  <GND * 1 %d %d 0 0 0 0>" % (x, y))

    # --- parts; each returns its pin coordinates
    def pac(self, x, y, num):
        self.add("Pac", "P%d" % num, x, y, 18, -26, 1,
                 [(str(num), 1), ("50 Ohm", 1), ("0 dBm", 0), ("1 GHz", 0)])
        return (x, y - 30), (x, y + 30)

    def res(self, name, x, y, value, rot=0):
        self.add("R", name, x, y, 15 if rot else -26, -26 if rot else 15, rot,
                 [(value, 1), ("26.85", 0), ("european", 0)])
        return ((x - 30, y), (x + 30, y)) if rot == 0 else ((x, y - 30), (x, y + 30))

    def ind(self, name, x, y, value, rot=0):
        self.add("L", name, x, y, 15 if rot else -26, -26 if rot else 15, rot, [(value, 1), ("", 0)])
        return ((x - 30, y), (x + 30, y)) if rot == 0 else ((x, y - 30), (x, y + 30))

    def cap(self, name, x, y, value, rot=0):
        self.add("C", name, x, y, 15 if rot else -26, -26 if rot else 15, rot, [(value, 1), ("", 0), ("neutral", 0)])
        return ((x - 30, y), (x + 30, y)) if rot == 0 else ((x, y - 30), (x, y + 30))

    def part2(self, kind, name, x, y, props):
        """Any two-pin line part (CLIN, CSTEP, MLIN, MSTEP): pins (-30,0) (+30,0).
        props: (key, value) in the order of the Qucs component; W, L, W1, W2 are shown."""
        shown = {"W", "L", "W1", "W2"}
        self.add(kind, name, x, y, -26, -88, 0, [(v, 1 if k in shown else 0) for k, v in props])
        return (x - 30, y), (x + 30, y)

    def subst(self, x, y, er, h):
        self.comp.append('  <SUBST Sub1 1 %d %d -30 24 0 0 "%s" 1 "%s" 1 "35 um" 1 "0.02" 1 "1.72e-8" 1 "0" 1>'
                         % (x, y, er, h))

    def tlin(self, name, x, y, z, length, alpha):
        self.add("TLIN", name, x, y, -26, -46, 0,
                 [(z, 1), (length, 0), (alpha, 0), ("26.85", 0)])
        return (x - 30, y), (x + 30, y)

    def spfile(self, name, x, y, fname, nports):
        self.add("SPfile", name, x, y, -26, -64 if nports == 3 else -44, 0,
                 [(fname, 0), ("rectangular", 0), ("linear", 0), ("open", 0), (str(nports), 0)])
        if nports == 3:
            return (x - 30, y - 30), (x + 30, y - 30), (x - 30, y + 30), (x, y + 60)
        if nports == 1:
            return (x - 30, y), (x, y + 30)
        return (x - 30, y), (x + 30, y), (x, y + 30)

    def sweep_and_plot(self, x, y):
        self.add(".SP", "SP1", x, y, 0, 51, 0,
                 [("log", 1), ("1 MHz", 1), ("10 GHz", 1), ("401", 1), ("no", 0), ("1", 0), ("2", 0)])
        self.add("Eqn", "Eqn1", x + 220, y, -23, 12, 0, [("S11_dB=dB(S[1,1])", 1), ("yes", 0)])

    def text(self):
        vw = 1500
        vh = 1150
        out = [HEADER.format(name=self.name, vw=vw, vh=vh), "<Components>"]
        out += self.comp
        out += ["</Components>", "<Wires>"] + self.wires + ["</Wires>", "<Diagrams>"]
        out += ['  <Rect 100 1080 520 320 3 #c0c0c0 1 10 1 1e+06 1 1e+10 1 -40 10 2 1 -1 1 1 315 0 225 "Frequency, Hz" "S11, dB" "">',
                '\t<"S11_dB" #ff0000 0 3 0 0 0>', "  </Rect>", "</Diagrams>", "<Paintings>"]
        out += ['  <Text 100 700 12 #000000 0 "%s\\nRun: Simulate (F2).">' % self.title.replace('"', "'"), "</Paintings>"]
        return "\n".join(out) + "\n"


def A_case(name, title, chip):
    s = Sch(name)
    s.title = title
    p1, gp = s.pac(60, 260, 1)
    s.gnd(*gp)
    em = s.spfile("EM", 300, 260, str(HERE / st.S3P), 3)
    s.path(p1, (270, 230))                    # port 1 -> EM pin 1
    s.path(em[3], (300, 340))
    s.gnd(300, 340)
    if chip:
        c = s.spfile("CHIP", 460, 230, str(HERE / st.S2P), 2)
        s.path(em[1], c[0])                   # EM pin 2 -> chip pin 1 (INHI)
        s.path(c[2], (460, 280))
        s.gnd(460, 280)
        s.path(c[1], (520, 230), (520, 400), (240, 400), (240, 290), em[2])  # chip pin 2 (INLO) -> EM pin 3
    else:
        a, _ = s.res("RP2", 380, 260, "50 Ohm", 1)
        s.path(em[1], (380, 230))
        s.gnd(380, 290)
        s.res("RP3", 230, 320, "50 Ohm", 1)
        s.path(em[2], (230, 290))
        s.gnd(230, 350)
    s.sweep_and_plot(60, 480)
    return s


def tlin_values(netlist):
    return {m.group(1): (m.group(2), m.group(3), m.group(4))
            for m in re.finditer(r'TLIN:(\w+) \S+ \S+ Z="([^"]+)" L="([^"]+)" Alpha="([^"]+)"', netlist)}


def B_case(name, title, load, z50=False, chip_file=None):
    net, _ = st.netlist_B(load, z50=z50, chip_file=chip_file)
    tl = tlin_values(net)
    s = Sch(name)
    s.title = title
    p1, gp = s.pac(60, 230, 1)
    s.gnd(*gp)
    y = 200
    prev = p1
    x = 130
    pins = {}
    for nm in ("PAD", "L1", "L2", "L3"):
        a, b = s.tlin(nm, x, y, *tl[nm])
        s.path(prev, a)
        pins[nm] = (a, b)
        prev = b
        x += 120
    # shunt resistors 2 x 100 ohm with 0.25 nH, at the right pin of L1 and of L2
    for nm, node in (("R1", pins["L1"][1]), ("R2", pins["L2"][1])):
        top, bot = s.res(nm, node[0], node[1] + 60, "100 Ohm", 1)
        s.path(node, top)
        l_top, l_bot = s.ind(nm + "L", node[0], node[1] + 120, "0.25 nH", 1)
        s.gnd(*l_bot)
    # C1: 47 nF + 0.25 nH + 0.035 ohm in series, then L4 to the chip
    a, b = s.cap("C1", x, y, "47 nF"); s.path(prev, a); prev = b; x += 120
    a, b = s.ind("C1L", x, y, "0.25 nH"); s.path(prev, a); prev = b; x += 120
    a, b = s.res("C1R", x, y, "0.035 Ohm"); s.path(prev, a); prev = b; x += 120
    a, b = s.tlin("L4", x, y, *tl["L4"]); s.path(prev, a); prev = b; x += 120
    if load == "chip":
        c = s.spfile("CHIP", x, y, str(HERE / (chip_file or st.S2P)), 2)
        s.path(prev, c[0])
        s.path(c[2], (x, y + 60))
        s.gnd(x, y + 60)
        prev = c[1]
        x += 120
    else:                                     # 50 ohm from each pad to ground, nets not joined
        r_top, r_bot = s.res("RP2", prev[0], prev[1] + 60, "50 Ohm", 1)
        s.path(prev, r_top)
        s.gnd(*r_bot)
        prev = (x - 30, y)                    # INLO net = left pin of L5, 30 units from the INHI net
        r_top, r_bot = s.res("RP3", prev[0], prev[1] + 60, "50 Ohm", 1)
        s.path(prev, r_top)
        s.gnd(*r_bot)
    a, b = s.tlin("L5", x, y, *tl["L5"]); s.path(prev, a); prev = b; x += 120
    a, b = s.cap("C2", x, y, "47 nF"); s.path(prev, a); prev = b; x += 120
    a, b = s.ind("C2L", x, y, "0.25 nH"); s.path(prev, a); prev = b; x += 120
    a, b = s.res("C2R", x, y, "0.035 Ohm"); s.path(prev, a); prev = b
    s.gnd(*b)
    s.sweep_and_plot(60, 480)
    return s


def _chain_case(name, title, el, taps, plane):
    """Draw a chain from run_s11_study.chain_C / run_adl5507_study.chain_E.

    el: the series elements (the chip SPfile has no `b` for a 1-port); taps: (name, node, ohm),
    a shunt resistor with 0.25 nH to ground on that node.
    """
    sb = st.SUBSTRATES[plane]
    s = Sch(name)
    s.title = title
    p1, gp = s.pac(60, 230, 1)
    s.gnd(*gp)
    y, x, prev = 200, 130, p1
    pins = {}
    for e in el:
        one_port = e["kind"] == "SPfile" and not e.get("b")
        if e["kind"] in ("R", "L", "C"):
            a, b = {"R": s.res, "L": s.ind, "C": s.cap}[e["kind"]](e["name"], x, y, e["props"][0][1])
        elif e["kind"] == "SPfile":
            c = s.spfile("CHIP", x, y, str(HERE / e["props"][0][1]), 1 if one_port else 2)
            a, b = c[0], (c[0] if one_port else c[1])
            ref = c[1] if one_port else c[2]
            s.path(ref, (ref[0], ref[1] + 30))
            s.gnd(ref[0], ref[1] + 30)
        else:
            a, b = s.part2(e["kind"], e["name"], x, y, e["props"])
        s.path(prev, a)
        pins[e["b"] if e.get("b") else e["a"]] = b
        prev = b
        x += 120
    if not (el[-1]["kind"] == "SPfile" and not el[-1].get("b")):
        s.gnd(*prev)
    for nm, node, ohm in taps:
        n = pins[node]
        top, bot = s.res(nm, n[0], n[1] + 60, "%g Ohm" % ohm, 1)
        s.path(n, top)
        _, l_bot = s.ind(nm + "L", n[0], n[1] + 120, "0.25 nH", 1)
        s.gnd(*l_bot)
    s.subst(900, 500, sb["er"], sb["h"])
    s.sweep_and_plot(60, 480)
    return s


def C_case(name, title, line, plane, chip_file=None, geom=None):
    """Substrate-defined lines (SUBST + CLIN/CSTEP or MLIN/MSTEP), see run_s11_study.chain_C."""
    _, el, taps = st.chain_C(line, plane, chip_file, geom)
    r_shunt = st.merged_geom(geom)["r_shunt"]
    return _chain_case(name, title, el, [(nm, node, r_shunt) for nm, node in taps], plane)


def E_case(name, title, line, plane, mode):
    """ADL5507 on the same lines, see run_adl5507_study.chain_E."""
    import run_adl5507_study as ea
    _, el, taps = ea.chain_E(line, plane, mode)
    return _chain_case(name, title, el, taps, plane)


def E0_case(name, title, chip_file, shunt_ohm=None):
    """A detector input alone at a 50 ohm port (1-port file, optional shunt resistor)."""
    s = Sch(name)
    s.title = title
    p1, gp = s.pac(60, 230, 1)
    s.gnd(*gp)
    c = s.spfile("CHIP", 200, 200, str(HERE / chip_file), 1)
    s.path(p1, (140, 200), c[0])             # the wire is split where the shunt resistor taps it
    s.path(c[1], (200, 260))
    s.gnd(200, 260)
    if shunt_ohm:
        top, bot = s.res("RT", 140, 260, "%g Ohm" % shunt_ohm, 1)
        s.path((140, 200), top)
        s.gnd(*bot)
    s.sweep_and_plot(60, 480)
    return s


CASES = {
    "case_A0_rfsim_50ohm": lambda n: A_case(n, "A0: rfsim 3-port (EM), ports 2 and 3 terminated 50 ohm", False),
    "case_A1_rfsim_ad8317": lambda n: A_case(n, "A1: rfsim 3-port (EM) + AD8317 input between INHI and INLO", True),
    "case_B_circuit_ad8317": lambda n: B_case(n, "B: circuit model of J1 to U1 + AD8317 input (datasheet table)", "chip"),
    "case_B_chart_circuit_ad8317": lambda n: B_case(
        n, "B_chart: as B, AD8317 input from the Figure 15 chart at >= 5.8 GHz", "chip", chip_file=st.S2P_CHART),
    "case_B0_circuit_50ohm": lambda n: B_case(n, "B0: circuit model, 50 ohm on INHI and on INLO", "50"),
    "case_C1_cpw_bcu_ad8317": lambda n: C_case(n, "C1: coplanar lines (CLIN) on SUBST, ground plane B.Cu 1.44 mm (as built) + AD8317", "cpw", "bcu"),
    "case_C1c_cpw_bcu_chart_ad8317": lambda n: C_case(n, "C1c: as C1, AD8317 chart points >= 5.8 GHz", "cpw", "bcu", st.S2P_CHART),
    "case_C2_cpw_in1_ad8317": lambda n: C_case(n, "C2: coplanar lines, ground plane In1 0.1 mm (what if In1 were solid) + AD8317", "cpw", "in1"),
    "case_C3_ms_in1_ad8317": lambda n: C_case(n, "C3: microstrip (MLIN) over In1 0.1 mm (what if) + AD8317", "ms", "in1"),
    "case_C4_ms_bcu_ad8317": lambda n: C_case(n, "C4: microstrip (MLIN) over B.Cu 1.44 mm + AD8317", "ms", "bcu"),
    "case_B2_circuit_z50_ad8317": lambda n: B_case(n, "B2: circuit model with 50 ohm traces + AD8317", "chip", z50=True),
}

# ADL5507 cases (run_adl5507_study.py): the chips alone, and the board on the four stackups
_ADL = "ADL5507_RFIN_1MHz_10GHz_raw.s1p", "ADL5507_RFIN_1MHz_10GHz_shunt51.s1p"
CASES.update({
    "case_E_A0r_ad8317_bare": lambda n: E0_case(n, "A0r: AD8317 INHI to ground, bare", "AD8317_INHI_1MHz_10GHz.s1p"),
    "case_E_A0t_ad8317_52R": lambda n: E0_case(n, "A0t: AD8317 INHI with the 52.3 ohm of the datasheet test circuit",
                                               "AD8317_INHI_1MHz_10GHz.s1p", 52.3),
    "case_E0r_adl5507_bare": lambda n: E0_case(n, "E0r: ADL5507 bare RFIN (datasheet Table 5)", _ADL[0]),
    "case_E0s_adl5507_table4": lambda n: E0_case(n, "E0s: ADL5507 with 51 ohm (datasheet Table 4)", _ADL[1]),
    "case_E0i_adl5507_51R": lambda n: E0_case(n, "E0i: ADL5507 bare RFIN with an ideal 51 ohm", _ADL[0], 51.0),
})
for _line, _plane in (("cpw", "bcu"), ("ms", "bcu"), ("cpw", "in1"), ("ms", "in1")):
    _tag = "%s_%s" % (_line, _plane)
    for _key, _mode, _what in (("E1", "dropin", "ADL5507 drop-in (R1, R2, C1 as on the AD8317 board)"),
                               ("E2", "adi", "ADL5507 with one 51 ohm at the pin (ADI reference)"),
                               ("E3", "table", "ADL5507 as datasheet Table 4 (51 ohm included)")):
        CASES["case_%s_%s_adl5507_%s" % (_key, _tag, _mode)] = (
            lambda n, l=_line, p=_plane, m=_mode, w=_what: E_case(n, "E: %s on %s, %s" % (w, l, p), l, p, m))
    CASES["case_E_C_%s_ad8317" % _tag] = (
        lambda n, l=_line, p=_plane: C_case(n, "C: AD8317 on %s, %s (as in run_s11_study)" % (l, p), l, p))


def main():
    for name, make in CASES.items():
        sch = make(name)
        (HERE / (name + ".sch")).write_text(sch.text(), encoding="ascii", newline="\n")
        print("written", name + ".sch")


if __name__ == "__main__":
    main()
