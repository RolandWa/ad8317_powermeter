"""
Headless EMerge test runner and artifact reviewer.

Runs the same flow as the KiCad plugin without launching KiCad UI:
1) Export Gerbers with kicad-cli
2) Build emerge_job.json
3) Invoke emerge_runner.py --job ...
4) Review all output artifacts and write summary reports

Usage examples:
  python sim/emerge/headless_emerge_test.py --run
  python sim/emerge/headless_emerge_test.py --review-only
  python sim/emerge/headless_emerge_test.py --output-dir sim/emerge/results --debug
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime
from typing import Any

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from gerber_exporter import GerberExporter


HERE = pathlib.Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
DEFAULT_PCB = PROJECT / "kicad" / "ad8317_powermeter.kicad_pcb"
DEFAULT_CONFIG = HERE / "plugin" / "emerge_config.toml"
DEFAULT_OUTPUT_DIR = HERE / "results"


def _read_toml(path: pathlib.Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _load_ports(cfg: dict[str, Any]) -> dict[str, Any]:
    ports = {}
    for name, pd in cfg.get("ports", {}).items():
        ports[name] = {
            "pad": pd.get("pad", ""),
            "R": float(pd.get("R", 50.0)),
            "C": pd.get("C"),
            "active": bool(pd.get("active", True)),
            "dir": str(pd.get("dir", "z")),
        }
    return ports


def _build_job(pcb_path: pathlib.Path,
               output_dir: pathlib.Path,
               gerber_dir: pathlib.Path,
               cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "pcb_path": str(pcb_path),
        "gerber_dir": str(gerber_dir),
        "output_dir": str(output_dir / "touchstone"),
        "port_defs": _load_ports(cfg),
        "sweep": dict(cfg.get("sweep", {})),
        "thresholds": dict(cfg.get("thresholds", {})),
        "solver": dict(cfg.get("solver", {})),
        "visualization": dict(cfg.get("visualization", {})),
        "passives": dict(cfg.get("passives", {})),
        "gerber": {
            "use_gerbers": bool(cfg.get("gerber", {}).get("use_gerbers", True)),
        },
        "mesh": dict(cfg.get("mesh", {})),
    }


def _count_gerber_elements(path: pathlib.Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    d01 = sum(1 for ln in lines if re.search(r"D0*1\*", ln))
    d02 = sum(1 for ln in lines if re.search(r"D0*2\*", ln))
    d03 = sum(1 for ln in lines if re.search(r"D0*3\*", ln))
    g36 = sum(1 for ln in lines if "G36*" in ln)
    g37 = sum(1 for ln in lines if "G37*" in ln)
    return {
        "D01": d01,
        "D02": d02,
        "D03": d03,
        "G36": g36,
        "G37": g37,
        "total": d01 + d02 + d03 + g36 + g37,
    }


def _analyze_s2p(ts_path: pathlib.Path) -> dict[str, Any]:
    if not ts_path.exists():
        return {"exists": False}
    lines = ts_path.read_text(encoding="utf-8", errors="replace").splitlines()
    data_lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("!") and not ln.strip().startswith("#")]
    return {
        "exists": True,
        "size_bytes": ts_path.stat().st_size,
        "line_count": len(lines),
        "data_points": len(data_lines),
        "first_data_line": data_lines[0] if data_lines else "",
        "last_data_line": data_lines[-1] if data_lines else "",
    }


def _review_outputs(output_dir: pathlib.Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "files": [],
        "gerber_counts": {},
        "result": {},
        "report": {},
        "touchstone": {},
        "highlights": [],
    }

    files = sorted([p for p in output_dir.rglob("*") if p.is_file()])
    for p in files:
        out["files"].append({
            "path": str(p.relative_to(output_dir)).replace("\\", "/"),
            "size_bytes": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
        })

    for rel in ["gerbers", "gerbers/_cropped", "gerbers/_sanitized"]:
        gp = output_dir / rel
        rows = []
        if gp.is_dir():
            for gbr in sorted(gp.glob("*.gbr")):
                cnt = _count_gerber_elements(gbr)
                rows.append({
                    "file": gbr.name,
                    "size_bytes": gbr.stat().st_size,
                    **cnt,
                })
        out["gerber_counts"][rel] = rows

    result_path = output_dir / "emerge_job.result.json"
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            result = {"parse_error": str(exc)}
        out["result"] = result

        logs = result.get("log", []) if isinstance(result, dict) else []
        focus_line = next((ln for ln in logs if "Mesh focus summary:" in str(ln)), "")
        viol_line = next((ln for ln in logs if "Total violations:" in str(ln)), "")
        mesh_attempt = next((ln for ln in logs if "Mesh attempt" in str(ln)), "")
        if focus_line:
            out["highlights"].append(focus_line)
        if viol_line:
            out["highlights"].append(viol_line)
        if mesh_attempt:
            out["highlights"].append(mesh_attempt)

        ts_path = pathlib.Path(result.get("ts_path", "")) if isinstance(result, dict) else pathlib.Path()
        if ts_path:
            out["touchstone"] = _analyze_s2p(ts_path)
    else:
        out["result"] = {"missing": str(result_path)}

    report_path = output_dir / "emerge_report.txt"
    if report_path.exists():
        lines = report_path.read_text(encoding="utf-8", errors="replace").splitlines()
        out["report"] = {
            "exists": True,
            "path": str(report_path),
            "line_count": len(lines),
        }
        keys = (
            "Stage 3 / 5",
            "Mesh focus summary:",
            "Mesh attempt",
            "still running",
            "Total violations:",
            "ERROR:",
        )
        for ln in lines:
            if any(k in ln for k in keys):
                out["highlights"].append(ln)
    else:
        out["report"] = {"exists": False, "missing": str(report_path)}

    # Keep highlights unique while preserving order.
    seen = set()
    uniq = []
    for h in out["highlights"]:
        if h in seen:
            continue
        seen.add(h)
        uniq.append(h)
    out["highlights"] = uniq

    return out


def _write_review_reports(output_dir: pathlib.Path, review: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path]:
    json_path = output_dir / "emerge_output_review.json"
    md_path = output_dir / "emerge_output_review.md"

    json_path.write_text(json.dumps(review, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# EMerge Headless Output Review")
    lines.append("")
    lines.append(f"- Timestamp: {review['timestamp']}")
    lines.append(f"- Output dir: {review['output_dir']}")
    lines.append(f"- Files found: {len(review['files'])}")
    lines.append("")

    lines.append("## Highlights")
    if review.get("highlights"):
        for h in review["highlights"]:
            lines.append(f"- {h}")
    else:
        lines.append("- No highlight lines found in solver log.")
    lines.append("")

    lines.append("## Gerber Element Counts")
    for rel, rows in review.get("gerber_counts", {}).items():
        lines.append("")
        lines.append(f"### {rel}")
        if not rows:
            lines.append("- No Gerber files")
            continue
        lines.append("| File | KB | D01 | D02 | D03 | G36 | G37 | Total |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for r in rows:
            lines.append(
                f"| {r['file']} | {r['size_bytes']/1024:.1f} | {r['D01']} | {r['D02']} | {r['D03']} | {r['G36']} | {r['G37']} | {r['total']} |"
            )

    ts = review.get("touchstone", {})
    lines.append("")
    lines.append("## Touchstone")
    if ts.get("exists"):
        lines.append(f"- Size: {ts.get('size_bytes', 0)} bytes")
        lines.append(f"- Data points: {ts.get('data_points', 0)}")
    else:
        lines.append("- Touchstone file missing")

    lines.append("")
    lines.append("## Files")
    for f in review.get("files", []):
        lines.append(f"- {f['path']} ({f['size_bytes']} bytes)")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run EMerge headless and review output artifacts")
    parser.add_argument("--pcb", default=str(DEFAULT_PCB), help="Path to .kicad_pcb")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to emerge_config.toml")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for job/results/gerbers")
    parser.add_argument("--run", action="store_true", help="Run export + solver before review")
    parser.add_argument("--review-only", action="store_true", help="Skip run, only review existing outputs")
    parser.add_argument("--debug", action="store_true", help="Pass --debug to emerge_runner")
    args = parser.parse_args()

    pcb_path = pathlib.Path(args.pcb).resolve()
    cfg_path = pathlib.Path(args.config).resolve()
    out_dir = pathlib.Path(args.output_dir).resolve()
    gerber_dir = out_dir / "gerbers"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.review_only:
        cfg = _read_toml(cfg_path)
        python_exe = str(cfg.get("python", {}).get("python_exe", "")).strip() or sys.executable

        report_lines: list[str] = []
        copper_layers = cfg.get("gerber", {}).get("layers", {}).get("copper")
        exporter = GerberExporter(
            pcb_path=pcb_path,
            output_dir=gerber_dir,
            kicad_cli=str(cfg.get("gerber", {}).get("kicad_cli", "")),
            report_lines=report_lines,
            verbose=True,
            copper_layers=copper_layers,
        )
        exported = exporter.run()
        if not exported:
            print("ERROR: Gerber export failed")
            return 2

        job = _build_job(pcb_path=pcb_path, output_dir=out_dir, gerber_dir=gerber_dir, cfg=cfg)
        job_path = out_dir / "emerge_job.json"
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

        runner = HERE / "emerge_runner.py"
        cmd = [python_exe, str(runner), "--job", str(job_path)]
        if args.debug:
            cmd.append("--debug")

        print("Running solver:")
        print(" ".join(cmd))
        rc = subprocess.run(cmd, cwd=str(PROJECT)).returncode
        if rc != 0:
            print(f"WARNING: emerge_runner exited with code {rc}")

    review = _review_outputs(out_dir)
    json_path, md_path = _write_review_reports(out_dir, review)

    print("")
    print("Headless review complete")
    print(f"- Review JSON: {json_path}")
    print(f"- Review MD  : {md_path}")
    print(f"- Files      : {len(review.get('files', []))}")
    if review.get("highlights"):
        print("- Highlights :")
        for h in review["highlights"]:
            print(f"  {h}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
