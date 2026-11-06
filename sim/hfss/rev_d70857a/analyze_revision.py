"""Evaluate the revised, solved 7-port PCB with explicit detector/component loads."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

import circuit_post as circuit

ROOT = Path(__file__).resolve().parent
MODELS = {
    "ideal": None,
    "generic_0402": circuit.REAL_PARTS,
    "user_assumption": circuit.USER_PARTS,
}
BANDS = [(0.1, 1), (1, 3), (3, 6), (6, 8), (8, 10)]


def summarize(result, low=0.1, high=10):
    freq = result["freq_hz"]
    mask = (freq >= low * 1e9) & (freq <= high * 1e9)
    if not mask.any():
        raise RuntimeError(f"No samples in required band {low}..{high} GHz")
    s11, transfer, f = [result[k][mask] for k in ("s11_db", "h_inc_db", "freq_hz")]
    return {
        **circuit.objective(result, (low * 1e9, high * 1e9)),
        "worst_s11_f_ghz": float(f[np.argmax(s11)] / 1e9),
        "minimum_transfer_db": float(transfer.min()),
        "minimum_transfer_f_ghz": float(f[np.argmin(transfer)] / 1e9),
        "mean_chip_absorbed_power_fraction": float(result["p_chip"][mask].mean()),
    }


def evaluate(path, detector, model):
    freq, s, order = circuit.read_touchstone(path)
    if order != circuit.EXPECTED_PORTS or s.shape != (len(freq), 7, 7):
        raise RuntimeError(f"Unexpected network shape or port order in {path}")
    if not np.all(np.diff(freq) > 0) or not np.isfinite(s).all():
        raise RuntimeError(f"Nonfinite or nonmonotonic Touchstone data in {path}")
    header = next(line for line in path.read_text().splitlines() if line.startswith("#"))
    tokens = header.upper().split()
    if "R" not in tokens or float(tokens[tokens.index("R") + 1]) != 50:
        raise RuntimeError(f"Expected real 50-ohm reference impedances: {header}")
    result = circuit.evaluate(path, detector=detector, parasitics=model)
    for key, value in result.items():
        if not np.isfinite(value).all():
            raise RuntimeError(f"Nonfinite loaded quantity {key} in {path}")
    loss = result["accepted"] - result["p_chip"] - result["p_resistors"] - result["p_caps"]
    singular_max = float(np.linalg.svd(s, compute_uv=False).max())
    if singular_max > 1.01 or loss.min() < -0.01 or result["accepted"].min() < -0.01:
        raise RuntimeError(f"Passivity/power-balance check failed for {path}")
    return result, {
        "touchstone_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "samples": len(freq),
        "frequency_range_ghz": [float(freq[0] / 1e9), float(freq[-1] / 1e9)],
        "max_em_singular_value": singular_max,
        "minimum_em_loss_fraction": float(loss.min()),
        "summary_0.1_10ghz": summarize(result),
        "bands": {f"{a:g}-{b:g}GHz": summarize(result, a, b) for a, b in BANDS},
    }


def create_pdf(out, rows, paragraphs):
    font = Path(r"C:\Windows\Fonts\arial.ttf")
    if not font.is_file():
        raise FileNotFoundError(font)
    pdfmetrics.registerFont(TTFont("ReportArial", str(font)))
    styles = getSampleStyleSheet()
    style = ParagraphStyle(
        "RevisionBody", parent=styles["BodyText"], fontName="ReportArial",
        fontSize=9, leading=12, spaceAfter=5,
    )
    heading = ParagraphStyle("RevisionTitle", parent=style, fontSize=15, leading=19)
    story = [Paragraph("AD8317 — rewizja PCB wip_40b50f97 (niezacommitowana)", heading), Spacer(1, 4 * mm)]
    for text in paragraphs:
        story.append(Paragraph(escape(text), style))
    cells = [["Sieć / model", "Najgorsze S11", "Pasmo S11 < −10 dB", "Rozrzut H"]]
    for label, metrics in rows:
        cells.append([
            label, f"{metrics['worst_s11_db']:.2f} dB",
            f"{100 * metrics['fraction_below_minus10']:.1f}%",
            f"{metrics['h_flatness_db']:.2f} dB",
        ])
    table = Table([[Paragraph(escape(str(cell)), style) for cell in row] for row in cells],
                  colWidths=[64 * mm, 32 * mm, 47 * mm, 31 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story += [Spacer(1, 3 * mm), table, PageBreak(),
              Paragraph("Odpowiedź całego obciążonego toru RF", heading),
              Image(str(out / "loaded_comparison.png"), width=174 * mm, height=139 * mm)]
    story.append(Paragraph(
        "H = 20 log10(|V(INHI) − V(INLO)| / √50), dla fali padającej o mocy 1 W. "
        "Nie jest to S21 ani moc absorbowana przez detektor. Wyniki odnoszą się do modeli "
        "małosygnałowych; nie zastępują kalibracji miernika.", style,
    ))
    story += [
        PageBreak(), Paragraph("Geometria nowej rewizji", heading),
        Image(str(ROOT / "rf_model.png"), width=174 * mm, height=106.3 * mm),
        Paragraph("Widok ISO SMA + wycięty fragment PCB. Port P1 pozostaje na "
                  "wcześniej zaakceptowanej fizycznej płaszczyźnie SMA. "
                  "Snapshot źródła i diagnostyczne kopie PCB są zapisane oddzielnie.", style),
    ]
    SimpleDocTemplate(
        str(out / "RAPORT_REWIZJI.pdf"), pagesize=(210 * mm, 297 * mm),
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
    ).build(story)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("s7p", type=Path)
    args = parser.parse_args()
    actual = args.s7p.resolve()
    if not actual.is_file():
        raise FileNotFoundError(actual)
    verified = json.loads((ROOT / "results" / "solution_verification.json").read_text())
    if verified["status"] != "SOLVED_CONVERGED_EXPORTED":
        raise RuntimeError("The revised solution has not passed native verification")
    if hashlib.sha256(actual.read_bytes()).hexdigest() != verified["s7p_sha256"]:
        raise RuntimeError("The supplied network is not the verified revised solution")
    out = ROOT / "analysis"
    out.mkdir(exist_ok=True)
    reference = ROOT / "reference_data"
    cases = {"actual": actual, "prev_e24b8f3": reference / "prev_e24b8f3.s7p",
             "old_baseline": reference / "baseline.s7p",
             "previous_lc_geometry": reference / "fx_opt11.s7p"}
    metadata, curves = {}, {}
    for name, path in cases.items():
        selected = MODELS if name == "actual" else {"user_assumption": circuit.USER_PARTS}
        for model_name, model in selected.items():
            key = f"{name}/{model_name}/table"
            result, stats = evaluate(path, "table", model)
            curves[key], metadata[key] = result, stats
            with (out / f"{name}_{model_name}_table.csv").open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["frequency_GHz", "S11_dB", "accepted_P_fraction",
                                 "H_inc_dB", "P_chip_fraction", "P_resistors_fraction",
                                 "P_caps_fraction"])
                for i, freq in enumerate(result["freq_hz"]):
                    writer.writerow([freq / 1e9] + [result[k][i] for k in (
                        "s11_db", "accepted", "h_inc_db", "p_chip", "p_resistors", "p_caps",
                    )])
    chart_result, chart_stats = evaluate(actual, "chart", circuit.USER_PARTS)
    curves["actual/user_assumption/chart"] = chart_result
    metadata["actual/user_assumption/chart"] = chart_stats
    if metadata["actual/ideal/table"]["samples"] != 1001:
        raise RuntimeError("The revised sweep must contain exactly 1001 samples")
    if not np.allclose(metadata["actual/ideal/table"]["frequency_range_ghz"],
                       [0.001, 10], rtol=0, atol=1e-9):
        raise RuntimeError("The revised sweep does not cover 1 MHz..10 GHz")

    labels = {
        "actual/user_assumption/table": "Nowa PCB — model użytkownika",
        "actual/ideal/table": "Nowa PCB — idealne R/C",
        "actual/generic_0402/table": "Nowa PCB — ogólny model 0402",
        "prev_e24b8f3/user_assumption/table": "Rewizja e24b8f3 — model użytkownika",
        "old_baseline/user_assumption/table": "Stara PCB — model użytkownika",
        "previous_lc_geometry/user_assumption/table": "Dawna geometria L/C — model użytkownika",
        "actual/user_assumption/chart": "Nowa PCB — detektor z wykresu",
    }
    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    for key, label in labels.items():
        result = curves[key]
        style = "-" if key == "actual/user_assumption/table" else "--"
        for axis, quantity in zip(axes, ("s11_db", "h_inc_db")):
            axis.plot(result["freq_hz"] / 1e9, result[quantity], style, label=label)
            axis.grid(True, alpha=0.3)
    axes[0].axhline(-10, color="black", lw=0.7)
    axes[0].set_ylabel("Obciążone S11 [dB]")
    axes[1].set_ylabel("H: Vdiff / √Pinc [dB względem √50]")
    axes[1].set_xlabel("Częstotliwość [GHz]")
    axes[1].set_xlim(0.1, 10)
    axes[0].legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(out / "loaded_comparison.png", dpi=180)
    plt.close(fig)

    provenance = json.loads((ROOT / "source_commit.json").read_text(encoding="utf-8-sig"))
    drc = json.loads((ROOT / "source_drc.json").read_text())
    stitching = json.loads((ROOT / "stitching_audit.json").read_text())
    rf_line = json.loads((ROOT / "rf_line_geometry.json").read_text())
    build = json.loads((ROOT / "verification.json").read_text())
    isolated = sum(not any(row["zone_contacts"].values()) for row in stitching)
    new = metadata["actual/user_assumption/table"]["summary_0.1_10ghz"]
    prev = metadata["prev_e24b8f3/user_assumption/table"]["summary_0.1_10ghz"]
    old = metadata["old_baseline/user_assumption/table"]["summary_0.1_10ghz"]
    low_band = metadata["actual/user_assumption/table"]["bands"]["0.1-1GHz"]
    passivity = metadata["actual/user_assumption/table"]["max_em_singular_value"]
    errors = sum(v["severity"] == "error" for v in drc["violations"])
    segs = []
    for seg in rf_line["segments"]:
        if seg.get("top_gnd_gap_min_mm") is None:
            continue
        kind = "grafika Cu" if seg.get("copper_graphic") else "ścieżka"
        segs.append(
            f"{seg['net']}: {kind} w={seg['width_mm']:.4f} mm, L={seg['length_mm']:.2f} mm, "
            f"odstęp GND Top {seg['top_gnd_gap_min_mm']:.3f}–{seg['top_gnd_gap_max_mm']:.3f} mm, "
            f"Z0≈{seg['z0_cpwg_t35um_ohm'][0]:.0f}–{seg['z0_cpwg_t35um_ohm'][1]:.0f} Ω"
        )
    paragraphs = [
        f"Źródło: gałąź {provenance['branch']}, HEAD {provenance['head'][:8]} + NIEZACOMMITOWANE zmiany "
        f"PCB/DRU/PRO (PCB SHA-256 {provenance['sha256']['kicad_pcb'][:8]}). "
        f"DRC (kicad-cli, refill): {len(drc['violations'])} naruszeń, {errors} błędów, "
        f"{len(drc['unconnected_items'])} niepołączonych. Zszycie: {30 - isolated}/30 PTH GND J1 "
        "ma kontakt z wypełnionymi płaszczyznami GND. Pad 9 U1 ma teraz sieć GND "
        "(4 vias GND + strefa F.Cu); vias w antipadzie In1 usunięte.",
        "Ścieżka RF (obowiązkowy opis): linia współpłaszczyznowa z masą (CPWG) na F.Cu nad płaszczyzną "
        "GND In1; prepreg 0,10 mm FR4 εr=4,4, tanδ=0,02; Cu 35 µm. Szerokości i odstępy z KiCad, "
        "Z0 quasi-statycznie (conformal mapping, korekta grubości 35 µm, bez ENIG): " + "; ".join(segs) + ". "
        "Przejście SMA→linia to custom pad J1-1 (x 140,40–143,95 mm, footprint 307_b0).",
        "Pokrycie i maska: sygnał i masa wokół linii są odkryte (okno F.Mask 134,38–150,09 × "
        "82,74–87,22 mm oraz okna boczne J1) – w tej rewizji modelu maskę WYCIĘTO zgodnie z rzeczywistymi "
        "kształtami F.Mask z KiCad (sondy: nad linią RF i INHI brak maski, poza oknem maska jest). "
        "Wykończenie ENIG (Ni ~3–6 µm, Au ~0,05–0,1 µm) NIE jest modelowane: miedź σ=5,8e7 S/m. "
        "Ni zwiększa straty przy GHz (efekt naskórkowy w Ni), dla ~5 mm linii szacunkowo <0,1–0,2 dB przy 10 GHz; "
        "wpływ na S11 jest pomijalny, na tłumienie – niewielki.",
        "Symulacja: HFSS 2026 R1, 7 portów (P1 = port falowy na licu SMA z PEC cap, porty skupione "
        "na INHI/INLO/R1/R2/C1/C2), adaptacja 1/5/10 GHz, ΔS = 0,02; "
        f"sweep 1 MHz–10 GHz, {verified['samples']} próbek. "
        f"Ostatni przebieg adaptacyjny: {verified['adaptive_passes'][-1]['pass']}; "
        f"ostatnie dwa ΔS: {verified['adaptive_passes'][-2]['max_delta_s']:.6f} / "
        f"{verified['adaptive_passes'][-1]['max_delta_s']:.6f}. Ocena dotyczy 0,1–10 GHz.",
        f"Wynik całego obciążonego toru (model użytkownika): najgorsze S11 "
        f"{new['worst_s11_db']:.2f} dB przy {new['worst_s11_f_ghz']:.3f} GHz; "
        f"S11 < −10 dB dla {100 * new['fraction_below_minus10']:.1f}% próbek; średnie S11 {new['mean_s11_db']:.2f} dB. "
        f"Rewizja e24b8f3: {prev['worst_s11_db']:.2f} dB przy {prev['worst_s11_f_ghz']:.3f} GHz, "
        f"{100 * prev['fraction_below_minus10']:.1f}%, średnio {prev['mean_s11_db']:.2f} dB. "
        f"Stara PCB: {old['worst_s11_db']:.2f} dB, {100 * old['fraction_below_minus10']:.1f}%, "
        f"średnio {old['mean_s11_db']:.2f} dB. Rozrzut H: nowa {new['h_flatness_db']:.2f} dB, "
        f"e24b8f3 {prev['h_flatness_db']:.2f} dB.",
        f"W zakresie 0,1–1 GHz: najgorsze S11 {low_band['worst_s11_db']:.2f} dB, "
        f"rozrzut H {low_band['h_flatness_db']:.2f} dB. Minimum H {new['minimum_transfer_db']:.2f} dB "
        f"przy {new['minimum_transfer_f_ghz']:.3f} GHz. S11 dotyczy całego miernika "
        "(SMA + linia + R1/R2 + C1/C2 + wejście AD8317), nie samego przejścia SMA.",
        f"Pasywność: największa wartość singularna macierzy S {passivity:.6f}; danych nie poprawiano. "
        "Bilans mocy obciążonej sieci sprawdzono. Wynik porównawczy, nie certyfikacja dokładności.",
        "Model użytkownika: C1=C2=47 nF 0402, ESL=0,7 nH, ESR=0,04 Ω; R1=R2=100 Ω. Modele pasożytów "
        "R i ogólny model 0402 są założeniami, nie modelami producenta. Wnętrze SMA (PTFE εr 2,1), "
        "stop lutowniczy (7e6 S/m) i lite przelotki (ścianka 25 µm) to przybliżenia inżynierskie.",
        "Różnice względem e24b8f3 w modelu: (1) pad 9 natywnie GND zamiast odtworzonego no-net, "
        "(2) usunięte vias z antipadu In1, (3) pełne zszycie 30 PTH GND, (4) maska zdjęta z okna RF "
        "(w e24b8f3 maska pokrywała linię; wpływu samej maski nie wydzielono osobną symulacją – "
        "różnica wyników obejmuje wszystkie cztery zmiany łącznie).",
        f"Wnioski: względem e24b8f3 poprawiło się średnie S11 ({prev['mean_s11_db']:.2f} → "
        f"{new['mean_s11_db']:.2f} dB) i udział pasma z S11 < −10 dB "
        f"({100 * prev['fraction_below_minus10']:.1f} → {100 * new['fraction_below_minus10']:.1f}%). "
        "Najgorsze S11 całego miernika nadal ok. −1,3 dB przy ~8 GHz; nawet z idealnymi R/C wynosi "
        f"{metadata['actual/ideal/table']['summary_0.1_10ghz']['worst_s11_db']:.2f} dB, a izolowane przejście "
        "307_b0 dawało −25,55 dB. Ograniczeniem powyżej ~3 GHz jest więc sieć zakończenia "
        "(R1/R2, C1/C2, pady i odcinki linii do AD8317 oraz impedancja wejścia detektora), nie samo "
        "złącze SMA. Ewentualną optymalizację L/C należy kierować na tę sieć, nie na przejście SMA.",
    ]
    rows = [(labels[key], metadata[key]["summary_0.1_10ghz"]) for key in labels]
    report = ["# AD8317 — wyniki rewizji wip_40b50f97 (PCB niezacommitowana)", ""]
    report += [text + "\n" for text in paragraphs]
    report += ["## Wyniki całego obciążonego toru: 0,1–10 GHz", "",
               "| Wariant | Najgorsze S11 | f tego S11 | Udział próbek S11 < −10 dB | Rozrzut H |",
               "|---|---:|---:|---:|---:|"]
    for label, metrics in rows:
        report.append(f"| {label} | {metrics['worst_s11_db']:.2f} dB | "
                      f"{metrics['worst_s11_f_ghz']:.3f} GHz | "
                      f"{100 * metrics['fraction_below_minus10']:.1f}% | "
                      f"{metrics['h_flatness_db']:.2f} dB |")
    report += ["", "H jest napięciem różnicowym względem fali padającej, nie S21 ani mocą detektora.",
               "", "## Nowa PCB, model użytkownika: wyniki pasmowe", "",
               "| Pasmo | Najgorsze S11 | Rozrzut H |", "|---|---:|---:|"]
    for band, metrics in metadata["actual/user_assumption/table"]["bands"].items():
        report.append(f"| {band} | {metrics['worst_s11_db']:.2f} dB | "
                      f"{metrics['h_flatness_db']:.2f} dB |")
    report += [
               "", "![Porównanie obciążonego toru](loaded_comparison.png)", "",
               "![Model nowej rewizji: SMA + PCB](../rf_model.png)", "",
               "Dane pasmowe, kontrola pasywności i hashe: [metrics.json](metrics.json).",
               "Krzywe w plikach CSV można ponownie analizować bez uruchamiania HFSS.",
               "Snapshot Git: [source_project](../source_project/). "
               "Diagnostyczne PCB z refill/solid nie są zatwierdzonymi poprawkami źródła."]
    (out / "metrics.json").write_text(json.dumps({
        "source": provenance, "solution": verified, "cases": metadata,
        "rf_line": rf_line, "build_receipt_status": build["status"],
    }, indent=2), encoding="utf-8")
    (out / "RAPORT_REWIZJI.md").write_text("\n".join(report), encoding="utf-8")
    create_pdf(out, rows, paragraphs)
    print(json.dumps({label: metrics for label, metrics in rows}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
