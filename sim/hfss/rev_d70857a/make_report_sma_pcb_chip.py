"""PDF report: (A) isolated SMA->PCB transition, (B) full SMA->AD8317 path with R1/R2/C1/C2."""

import json
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import skrf as rf
from matplotlib.patches import Rectangle
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import circuit_post as circuit

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "analysis" / "sma_pcb_chip_report"
SMA = Path(r"D:\Ansys_proj\SMA_transitions")
TRANSITION = SMA / "results" / "307_b0.s2p"
FIG = SMA / "report_pdf" / "figures"
S7P = ROOT / "results" / "RF_7port.s7p"
BANDS = [(0.01, 1), (1, 3), (3, 6), (6, 8), (8, 10)]
MODELS = {"Model użytkownika (C: ESL 0,7 nH, ESR 0,04 Ω; R: Coilcraft 0402DC-0N8)": circuit.USER_PARTS,
          "Ogólny model 0402 (C: ESL 0,45 nH; R: ESL 0,4 nH, Cp 0,06 pF)": circuit.REAL_PARTS,
          "Idealne R/C": None}


def vswr(db):
    g = 10 ** (db / 20)
    return (1 + g) / (1 - g)


def reduce_ports(s, keep, gammas):
    others = [p for p in range(s.shape[1]) if p not in keep]
    g = np.diag([gammas[p] for p in others])
    out = np.zeros((s.shape[0], len(keep), len(keep)), complex)
    for k, m in enumerate(s):
        soo = m[np.ix_(others, others)]
        out[k] = m[np.ix_(keep, keep)] + m[np.ix_(keep, others)] @ g @ np.linalg.solve(
            np.eye(len(others)) - soo @ g, m[np.ix_(others, keep)])
    return out


def band_rows(f, s11, extra, extra_fmt):
    rows = []
    for lo, hi in BANDS:
        m = (f >= lo) & (f <= hi)
        i = np.argmax(s11[m])
        rows.append([f"{lo:g}–{hi:g} GHz", f"{s11[m][i]:.2f} dB @ {f[m][i]:.2f} GHz",
                     f"{vswr(s11[m][i]):.2f}", extra_fmt(extra[m])])
    return rows


def schematic(path):
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.set_xlim(0, 11)
    ax.set_ylim(-0.5, 3.3)
    ax.axis("off")
    y = 2.4
    ax.plot([0.6, 8.0], [y, y], "k", lw=2)
    ax.add_patch(Rectangle((0.1, y - 0.35), 0.5, 0.7, fc="gold", ec="k"))
    ax.text(0.35, y + 0.55, "J1 SMA\n60312202114307", ha="center", fontsize=8)
    ax.text(1.6, y + 0.15, "CPWG 0,15 mm / gap 0,30 mm\npad J1-1 307_b0", fontsize=7, ha="center")
    for x, name in ((3.2, "R1 100 Ω"), (4.2, "R2 100 Ω")):
        ax.plot([x, x], [y, 1.65], "k")
        ax.add_patch(Rectangle((x - 0.12, 1.05), 0.24, 0.6, fc="w", ec="k"))
        ax.plot([x, x], [1.05, 0.5], "k")
        ax.plot([x - 0.2, x + 0.2], [0.5, 0.5], "k", lw=2)
        ax.text(x + 0.18, 1.3, name, fontsize=8)
    ax.text(3.7, 0.15, "R1‖R2 = 50 Ω (terminacja)", ha="center", fontsize=8)
    ax.plot([5.6, 5.6], [y - 0.25, y + 0.25], "k", lw=3)
    ax.plot([5.75, 5.75], [y - 0.25, y + 0.25], "k", lw=3)
    ax.plot([5.6, 5.75], [y, y], "w", lw=3)
    ax.text(5.68, y + 0.4, "C1 47 nF", ha="center", fontsize=8)
    ax.add_patch(Rectangle((8.0, 0.9), 1.8, 2.0, fc="#ddeeff", ec="k"))
    ax.text(8.9, 1.9, "AD8317\n(model s2p\nINHI–INLO)", ha="center", fontsize=8)
    ax.text(7.6, y + 0.12, "INHI", fontsize=8)
    ax.plot([6.6, 8.0], [1.3, 1.3], "k", lw=2)
    ax.text(7.6, 1.42, "INLO", fontsize=8)
    ax.plot([6.6, 6.6], [1.3, 1.15], "k")
    ax.plot([6.45, 6.75], [1.15, 1.15], "k", lw=3)
    ax.plot([6.45, 6.75], [1.0, 1.0], "k", lw=3)
    ax.plot([6.6, 6.6], [1.0, 0.5], "k")
    ax.plot([6.4, 6.8], [0.5, 0.5], "k", lw=2)
    ax.text(6.85, 0.75, "C2 47 nF", fontsize=8)
    ax.text(0.1, -0.25, "Porty HFSS: P1 SMA, P2 INHI, P3 INLO, P4 R1, P5 R2, P6 C1, P7 C2 "
            "(porty skupione w szczelinach elementów)", fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Part A: isolated transition
    tr = rf.Network(str(TRANSITION))
    if tr.nports != 2 or not np.allclose(tr.z0, 50):
        raise RuntimeError("Unexpected transition network")
    fa = tr.f / 1e9
    a11, a21 = tr.s_db[:, 0, 0], tr.s_db[:, 1, 0]
    fig, ax = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax[0].plot(fa, a11)
    ax[0].axhline(-20, color="k", lw=0.7, ls="--")
    ax[0].set_ylabel("S11 [dB]")
    ax[0].set_title("Przejście SMA 60312202114307 → CPWG (footprint 307_b0), model izolowany")
    ax[1].plot(fa, a21, color="tab:green")
    ax[1].set_ylabel("S21 [dB]")
    ax[1].set_xlabel("Częstotliwość [GHz]")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "A_transition.png", dpi=200)
    plt.close(fig)
    rows_a = band_rows(fa, a11, a21, lambda x: f"{x.min():.3f} dB")
    accepted_a = 1 - 10 ** (a11.max() / 10)

    # Part B: full path
    freq, s7, order = circuit.read_touchstone(S7P)
    if order != circuit.EXPECTED_PORTS:
        raise RuntimeError(order)
    fb = freq / 1e9
    results = {name: circuit.evaluate(S7P, detector="table", parasitics=p) for name, p in MODELS.items()}
    user = results[next(iter(MODELS))]
    fig, ax = plt.subplots(2, 1, figsize=(8, 6.5), sharex=True)
    for name, r in results.items():
        ax[0].plot(fb, r["s11_db"], label=name.split(" (")[0])
        ax[1].plot(fb, r["h_inc_db"], label=name.split(" (")[0])
    ax[0].plot(fa, a11, "k--", lw=1, label="Samo przejście SMA (część A)")
    ax[0].axhline(-10, color="k", lw=0.7)
    ax[0].set_ylabel("S11 na porcie SMA [dB]")
    ax[0].legend(fontsize=7)
    ax[1].set_ylabel("H = |Vdiff|/√Pinc [dB]")
    ax[1].set_xlabel("Częstotliwość [GHz]")
    ax[0].set_title("Cały tor SMA → AD8317 z R1/R2/C1/C2 (HFSS 7-port + model obwodowy)")
    for a in ax:
        a.grid(alpha=0.3)
        a.set_xlim(0, 10)
    fig.tight_layout()
    fig.savefig(OUT / "B_full_path.png", dpi=200)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.stackplot(fb, user["p_resistors"], user["p_chip"], user["p_caps"],
                 user["accepted"] - user["p_resistors"] - user["p_chip"] - user["p_caps"],
                 labels=["R1+R2", "AD8317", "C1+C2", "straty PCB/SMA/promieniowanie"], alpha=0.85)
    ax.plot(fb, user["accepted"], "k", lw=1, label="przyjęta = 1 − |S11|²")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(0, 10)
    ax.set_xlabel("Częstotliwość [GHz]")
    ax.set_ylabel("Udział mocy padającej")
    ax.set_title("Bilans mocy – model użytkownika")
    ax.legend(fontsize=7, loc="upper right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "B_power_balance.png", dpi=200)
    plt.close(fig)

    # Part C: SMA -> R1 pad, everything else removed (open)
    red = reduce_ports(s7, [0, 3], {p: 1.0 for p in range(7)})
    c11 = 20 * np.log10(np.abs(red[:, 0, 0]))
    c21 = 20 * np.log10(np.abs(red[:, 1, 0]))
    rf.Network(f=freq, f_unit="hz", s=red, z0=50).write_touchstone(str(OUT / "SMA_to_R1pad_others_open"))
    rows_c = band_rows(fb, c11, c21, lambda x: f"{x.min():.2f} dB")
    schematic(OUT / "B_schematic.png")

    rows_b = {}
    for name, r in results.items():
        rows_b[name] = band_rows(fb, r["s11_db"], r["h_inc_db"], lambda x: f"{x.max() - x.min():.2f} dB")
    low = (fb >= 0.01) & (fb <= 1)
    chip_frac = float(user["p_chip"][(fb >= 0.1) & (fb <= 1)].mean())
    verified = json.loads((ROOT / "results" / "solution_verification.json").read_text(encoding="utf-8-sig"))
    passes = verified["adaptive_passes"]
    rf_line = json.loads((ROOT / "rf_line_geometry.json").read_text())
    smax = float(np.linalg.svd(s7, compute_uv=False).max())

    font = Path(r"C:\Windows\Fonts\arial.ttf")
    pdfmetrics.registerFont(TTFont("RA", str(font)))
    pdfmetrics.registerFont(TTFont("RAB", r"C:\Windows\Fonts\arialbd.ttf"))
    base = getSampleStyleSheet()["BodyText"]
    st = ParagraphStyle("b", parent=base, fontName="RA", fontSize=9, leading=12, spaceAfter=5)
    h1 = ParagraphStyle("h1", parent=st, fontName="RAB", fontSize=15, leading=19, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=st, fontName="RAB", fontSize=11.5, leading=15, spaceBefore=6)
    cap = ParagraphStyle("c", parent=st, fontSize=8, leading=10, textColor=colors.darkgrey)

    def P(t, s=st):
        return Paragraph(escape(t), s)

    def table(head, rows, widths):
        t = Table([[P(c) for c in head]] + [[P(c) for c in r] for r in rows], colWidths=[w * mm for w in widths],
                  repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    def img(path, width_mm):
        from reportlab.lib.utils import ImageReader
        w, h = ImageReader(str(path)).getSize()
        return Image(str(path), width=width_mm * mm, height=width_mm * mm * h / w)

    seg_rows = []
    for s in rf_line["segments"]:
        if s.get("top_gnd_gap_min_mm") is None:
            continue
        seg_rows.append([s["net"].replace("Net-(", "").rstrip(")"), "grafika Cu" if s.get("copper_graphic") else "ścieżka",
                         f"{s['width_mm']:.4f}", f"{s['length_mm']:.2f}",
                         f"{s['top_gnd_gap_min_mm']:.3f}–{s['top_gnd_gap_max_mm']:.3f}",
                         f"{s['z0_cpwg_t35um_ohm'][0]:.0f}–{s['z0_cpwg_t35um_ohm'][1]:.0f}"])

    s = []
    s += [P("AD8317 – raport: przejście SMA → PCB oraz cały tor SMA → AD8317", h1),
          P("Rewizja PCB: wip_40b50f97 (gałąź feature/wurth-60312202114307-short-sma, HEAD e24b8f3 + "
            "niezacommitowane zmiany PCB/DRU). Złącze Würth 60312202114307, footprint 307_b0. "
            "Data: 2026-11-06."),
          P("Streszczenie", h2),
          P(f"(A) Samo przejście SMA → linia CPWG jest dobrze dopasowane: najgorsze S11 {a11.max():.2f} dB "
            f"(VSWR {vswr(a11.max()):.2f}) w 0,01–10 GHz, S21 ≥ {a21.min():.2f} dB; przyjmowane jest ≥ "
            f"{100 * accepted_a:.1f}% mocy podanej na port SMA."),
          P(f"(B) Cały tor SMA → AD8317 z R1/R2 (2×100 Ω), C1/C2 (47 nF 0402) i wejściem detektora: do ~1 GHz "
            f"S11 ≤ {user['s11_db'][(fb >= 0.1) & (fb <= 1)].max():.1f} dB, powyżej ~3 GHz dopasowanie "
            f"pogarsza się do {user['s11_db'][fb >= 0.1].max():.2f} dB przy "
            f"{fb[fb >= 0.1][np.argmax(user['s11_db'][fb >= 0.1])]:.2f} GHz. Przyczyną jest sieć zakończenia "
            "(indukcyjności pasożytnicze R/C, pady 0402, odcinki linii i wejście AD8317), a nie złącze."),
          P("(C) Dwuport SMA → pad R1 (pozostałe elementy niezamontowane) wycięty z 7-portu pokazuje, że już "
            "same rozwarte pady R2/C1/C2 i odcinki do AD8317 tworzą rezonanse > 3 GHz."),
          P("Ścieżka RF – parametry (obowiązkowe)", h2),
          P("Typ: linia współpłaszczyznowa z masą (CPWG) na F.Cu nad płaszczyzną GND In1. Stackup F.Cu–In1: "
            "prepreg FR4 0,10 mm, εr 4,4, tanδ 0,02; miedź 35 µm. Z0 policzono quasi-statycznie (conformal "
            "mapping z korektą grubości 35 µm), bez ENIG. Linia i masa wokół są odkryte (okna F.Mask z KiCad, "
            "model HFSS bez maski w tych oknach). Wykończenie ENIG nie jest modelowane (miedź σ = 5,8e7 S/m); "
            "szacunkowo dodaje < 0,1–0,2 dB strat przy 10 GHz, wpływ na S11 pomijalny."),
          table(["Sieć", "Rodzaj", "w [mm]", "L [mm]", "gap GND Top [mm]", "Z0 [Ω]"], seg_rows,
                [38, 22, 18, 16, 38, 22]),
          PageBreak(),
          P("A. Przejście SMA → płytka (model izolowany)", h1),
          P("Model HFSS: złącze 60312202114307 z dokumentacji/STEP, footprint 307_b0 (pad J1-1 0,75 mm, antypad "
            "In1 pod padem, zszycie GND wg footprintu – patrz rysunek warstw). P1 – port "
            "falowy na licu SMA, P2 – port na linii CPWG w = 0,15 mm, gap 0,30 mm (ta sama konfiguracja co "
            "przy J1 na płytce: 0,15 mm, gap 0,30–0,36 mm). Sweep 10 MHz – 10 GHz, 500 punktów."),
          ]
    for name, w in (("307_b0_assembly.png", 120), ("307_b0_cutaway.png", 120)):
        if (FIG / name).is_file():
            s += [img(FIG / name, w), P(name.replace(".png", ""), cap)]
    s += [PageBreak()]
    if (FIG / "307_b0_layers.png").is_file():
        s += [img(FIG / "307_b0_layers.png", 174), P("Warstwy footprintu 307_b0 (każda osobno).", cap)]
    s += [img(OUT / "A_transition.png", 160),
          table(["Pasmo", "Najgorsze S11", "VSWR", "Najgorsze S21"], rows_a, [30, 60, 25, 45]),
          PageBreak(),
          P("B. Cały tor SMA → AD8317 z R1, R2, C1, C2", h1),
          img(OUT / "B_schematic.png", 170),
          P("Topologia (z netlisty KiCad): J1-In → R1 i R2 (100 Ω każdy) do GND = terminacja 50 Ω; C1 47 nF "
            "szeregowo J1-In → INHI; C2 47 nF INLO → GND. HFSS: pełna geometria wyciętego fragmentu PCB "
            "(SMA, linia, pady 0402, U1, pad 9 = GND z 4 przelotkami, zszycie 30/30) jako 7-port; elementy "
            "i detektor dołączone obwodowo (AD8317: dwuport INHI–INLO z danych katalogowych)."),
          P(f"Rozwiązanie: HPC job 4251, {passes[-1]['pass']} przebiegów adaptacyjnych (ostatnie ΔS "
            f"{passes[-2]['max_delta_s']:.4f} / {passes[-1]['max_delta_s']:.4f}, kryterium 0,02), "
            f"{len(freq)} punktów 1 MHz – 10 GHz. Pasywność: max wartość singularna S = {smax:.4f}."),
          img(ROOT / "rf_model.png", 160), P("Model HFSS: widok ISO złącze + wycięty fragment PCB.", cap),
          PageBreak(),
          img(OUT / "B_full_path.png", 165),
          P("H = 20·log10(|V(INHI) − V(INLO)| / √50) przy fali padającej 1 W – miara napięcia, które widzi "
            "detektor; dla miernika mocy liczy się płaskość H (kalibrowalna), a nie tylko S11.", cap)]
    for name, rows in rows_b.items():
        s += [KeepTogether([P(name, h2), table(["Pasmo", "Najgorsze S11", "VSWR", "Rozrzut H"], rows, [30, 60, 25, 45])])]
    s += [PageBreak(), img(OUT / "B_power_balance.png", 165),
          P(f"W 0,1–1 GHz średnio {100 * chip_frac:.1f}% mocy padającej trafia do wejścia AD8317, reszta "
            "głównie do R1/R2 (zgodnie z ideą terminacji 50 Ω). Powyżej ~3 GHz rośnie moc odbita – "
            "niedopasowanie sieci zakończenia."),
          P("C. Dwuport SMA → pad R1 (poglądowo)", h1),
          P("Z macierzy 7-portu: P1 i P4 (szczelina R1) pozostawione, porty INHI/INLO/R2/C1/C2 rozwarte "
            "(elementy niezamontowane). Nie jest to czyste przejście – zawiera odcinki z rozwartymi padami. "
            "Plik: SMA_to_R1pad_others_open.s2p."),
          table(["Pasmo", "Najgorsze S11", "VSWR", "Najgorsze S21"], rows_c, [30, 60, 25, 45]),
          P("Wnioski i zalecenia", h1),
          P(f"1. Złącze z footprintem 307_b0 jest dopasowane bardzo dobrze (S11 ≤ {a11.max():.1f} dB do 10 GHz) – "
            "nie wymaga dalszej optymalizacji."),
          P("2. Ograniczeniem całego miernika powyżej ~3 GHz jest sieć R1/R2/C1/C2 + AD8317: indukcyjność "
            "terminacji 2×0402 (≈0,4–0,8 nH) i pady 0402 na linii, oraz pojemność/rezonans C1/C2 z ESL. "
            "Nawet z idealnymi R/C najgorsze S11 jest słabe, więc sama geometria padów/odcinków do U1 też ma "
            "znaczenie."),
          P("3. Możliwe kierunki: terminacja bliżej końca linii (R jako ostatni element, linia „przelotowa” przez "
            "pad), rezystor 0201 lub cienkowarstwowy RF 50 Ω, mniejsze pady/antypady pod R i C na In1, "
            "kompensacja L/C sieci zakończenia (nie przejścia SMA), krótsze odcinki do INHI/INLO."),
          P("4. Ponieważ miernik jest kalibrowany, istotna jest powtarzalność i płaskość H; do ~1 GHz tor jest "
            f"płaski (rozrzut H 0,1–1 GHz ≈ {rows_b[next(iter(MODELS))][0][3]})."),
          P("Założenia i ograniczenia", h2),
          P("Modele pasożytów R/C są założeniami (nie modelami producenta); model AD8317 z danych katalogowych "
            "(dwuport szeregowy INHI–INLO). Wnętrze SMA: PTFE εr 2,1; stop lutowniczy 7e6 S/m; przelotki lite "
            "(ścianka 25 µm). ENIG nie modelowany. Wyniki porównawcze – nie zastępują pomiaru VNA."),
          ]
    SimpleDocTemplate(str(OUT / "RAPORT_SMA_PCB_AD8317.pdf"), pagesize=(210 * mm, 297 * mm), leftMargin=16 * mm,
                      rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                      title="AD8317 SMA-PCB-AD8317").build(s)
    print(OUT / "RAPORT_SMA_PCB_AD8317.pdf")


if __name__ == "__main__":
    main()
