"""
build.py — Build and/or deploy the EMerge FEM Simulation KiCad plugin.

Usage
-----
    # Build PCM-compatible ZIP into sim/emerge/dist/
    python build.py

    # Build ZIP + deploy (unpack) into KiCad's 3rdparty/plugins folder
    python build.py --deploy

    # Dev iteration: copy source files directly to KiCad (no ZIP step)
    python build.py --dev-deploy

    # Specify KiCad version or plugin version
    python build.py --kicad-version 9.0 --version 1.1.0

Source layout
-------------
    sim/emerge/plugin/          plugin entry point + tooling   (THIS_DIR)
        __init__.py
        emerge_plugin.py
        emerge_config.toml      single config source
        emerge_icon.png / icon-24.png / icon-64.png
        metadata.json
        build.py                (this file)
        sync_to_kicad.ps1

    sim/emerge/                 engine modules                  (EMERGE_DIR)
        emerge_runner.py
        emerge_config_dialog.py
        gerber_exporter.py
        kicad_reader.py
        python_bridge.py
        emerge_pipeline.py      standalone CLI (not deployed to plugin)

PCM ZIP structure (KiCad Package Content Manager)
-------------------------------------------------
    plugins/
        __init__.py
        emerge_plugin.py
        emerge_config.toml
        emerge_runner.py
        emerge_config_dialog.py
        gerber_exporter.py
        kicad_reader.py
        python_bridge.py
        emerge_icon.png
        icon-24.png
        icon-64.png
    resources/
        icon.png                (64x64 — required by PCM)
    metadata.json
"""

import argparse
import json
import os
import pathlib
import platform
import shutil
import sys
import zipfile

# ── paths ─────────────────────────────────────────────────────────────────────
THIS_DIR   = pathlib.Path(__file__).parent.resolve()  # sim/emerge/plugin/
EMERGE_DIR = THIS_DIR.parent                          # sim/emerge/
DIST_DIR   = EMERGE_DIR / "dist"
STAGE_DIR  = DIST_DIR / "stage"

PLUGIN_NAME = "com_github_<github-user>_emerge_fem"

# Files that live in THIS_DIR (plugin/) and go into plugins/ in the ZIP
_PLUGIN_FILES = [
    "__init__.py",
    "emerge_plugin.py",
    "emerge_config.toml",
    "emerge_icon.png",
    "icon-24.png",
    "icon-64.png",
]

# Files that live in EMERGE_DIR (sim/emerge/) and go into plugins/ in the ZIP
_ENGINE_FILES = [
    "emerge_runner.py",
    "emerge_config_dialog.py",
    "gerber_exporter.py",
    "kicad_reader.py",
    "python_bridge.py",
]

# emerge_pipeline.py is intentionally excluded — it is the standalone CLI,
# not part of the KiCad plugin runtime.


def _kicad_plugin_dir(kicad_version: str) -> pathlib.Path:
    """Return the KiCad 3rdparty/plugins directory for this machine."""
    if platform.system() == "Windows":
        onedrive = (os.environ.get("OneDrive") or
                    str(pathlib.Path.home() / "<cloud-folder>"))
        return (pathlib.Path(onedrive) / "Simulation tools" / "KiCad" /
                kicad_version / "3rdparty" / "plugins")
    # Linux / macOS fallback
    return pathlib.Path.home() / ".local" / "share" / "kicad" / kicad_version / "3rdparty" / "plugins"


def _read_version_from_metadata() -> str:
    meta = THIS_DIR / "metadata.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            versions = data.get("versions", [])
            if versions:
                return versions[0].get("version", "1.0.0")
        except Exception:
            pass
    return "1.0.0"


# ── build helpers ─────────────────────────────────────────────────────────────

def _clean_stage():
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    (STAGE_DIR / "plugins").mkdir(parents=True)
    (STAGE_DIR / "resources").mkdir(parents=True)


def _stage_files():
    dest = STAGE_DIR / "plugins"
    missing = []

    for name in _PLUGIN_FILES:
        src = THIS_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            print(f"  [plugin]  {name}")
        else:
            print(f"  MISSING   {src}")
            missing.append(str(src))

    for name in _ENGINE_FILES:
        src = EMERGE_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            print(f"  [engine]  {name}")
        else:
            print(f"  MISSING   {src}")
            missing.append(str(src))

    # PCM resource icon
    icon64 = THIS_DIR / "icon-64.png"
    if icon64.exists():
        shutil.copy2(icon64, STAGE_DIR / "resources" / "icon.png")
        print("  [res]     icon.png")

    # metadata.json at ZIP root
    meta = THIS_DIR / "metadata.json"
    if meta.exists():
        shutil.copy2(meta, STAGE_DIR / "metadata.json")
        print("  [root]    metadata.json")

    return missing


def _make_zip(version: str) -> pathlib.Path:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / f"emerge_fem_simulation-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(STAGE_DIR.rglob("*")):
            if f.is_file():
                arcname = f.relative_to(STAGE_DIR)
                zf.write(f, arcname)
                print(f"  + {arcname}")
    size_kb = zip_path.stat().st_size // 1024
    print(f"\n  ZIP: {zip_path}  ({size_kb} kB)")
    return zip_path


def _deploy_from_zip(zip_path: pathlib.Path, kicad_version: str):
    """Unpack ZIP into the KiCad 3rdparty/plugins directory."""
    plugins_root = _kicad_plugin_dir(kicad_version)
    if not plugins_root.exists():
        print(f"  ERROR: KiCad plugins directory not found: {plugins_root}")
        print("  Is KiCad installed?  Check --kicad-version.")
        return False

    dest = plugins_root / PLUGIN_NAME
    if dest.exists():
        print(f"  Removing old install: {dest}")
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.startswith("plugins/") and not member.endswith("/"):
                rel = pathlib.PurePosixPath(member).relative_to("plugins")
                target = dest / pathlib.Path(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
                print(f"  -> {target.name}")

    print(f"\n  Deployed to: {dest}")
    return True


def _dev_deploy(kicad_version: str):
    """
    Copy source files directly to KiCad's plugin folder — no ZIP needed.
    Faster than build+deploy for development iteration.
    """
    plugins_root = _kicad_plugin_dir(kicad_version)
    if not plugins_root.exists():
        print(f"  ERROR: KiCad plugins directory not found: {plugins_root}")
        return False

    dest = plugins_root / PLUGIN_NAME
    dest.mkdir(parents=True, exist_ok=True)
    print(f"  Deploying to: {dest}")

    for name in _PLUGIN_FILES:
        src = THIS_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            print(f"  [plugin]  {name}")
        else:
            print(f"  MISSING   {src}")

    for name in _ENGINE_FILES:
        src = EMERGE_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            print(f"  [engine]  {name}")
        else:
            print(f"  MISSING   {src}")

    # Clear stale .pyc so KiCad picks up fresh source (may be locked if KiCad is open)
    pycache = dest / "__pycache__"
    if pycache.exists():
        try:
            shutil.rmtree(pycache)
            print("  Cleared __pycache__")
        except PermissionError:
            print("  __pycache__ locked (KiCad running?) — skipped; restart KiCad to reload")

    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Package and/or deploy the EMerge KiCad plugin")
    parser.add_argument("--deploy",      action="store_true",
                        help="Build ZIP then install into KiCad plugins directory")
    parser.add_argument("--dev-deploy",  action="store_true",
                        help="Copy source files directly to KiCad (fast dev iteration)")
    parser.add_argument("--kicad-version", default="9.0",
                        help="KiCad version string (default: 9.0)")
    parser.add_argument("--version",     default=None,
                        help="Plugin version string (default: read from metadata.json)")
    args = parser.parse_args()

    version = args.version or _read_version_from_metadata()

    # ── dev-deploy: copy from source, skip ZIP ───────────────────────────────
    if args.dev_deploy:
        print(f"\nEMerge dev-deploy → KiCad {args.kicad_version}")
        print("=" * 50)
        ok = _dev_deploy(args.kicad_version)
        if ok:
            print("\nDone. In KiCad: Tools → External Plugins → Refresh Plugins")
        return

    # ── normal build (+ optional deploy from ZIP) ────────────────────────────
    print(f"\nEMerge plugin build  v{version}")
    print("=" * 50)

    print("\nStaging files ...")
    _clean_stage()
    missing = _stage_files()
    if missing:
        print(f"\nWARNING: {len(missing)} file(s) missing from staging — ZIP may be incomplete.")

    print("\nBuilding ZIP ...")
    zip_path = _make_zip(version)

    if args.deploy:
        print(f"\nDeploying from ZIP → KiCad {args.kicad_version} ...")
        ok = _deploy_from_zip(zip_path, args.kicad_version)
        if ok:
            print("\nDone. In KiCad: Tools → External Plugins → Refresh Plugins")
    else:
        print("\nDone. To install:")
        print(f"  python build.py --deploy")
        print(f"  — or —")
        print(f"  python build.py --dev-deploy   (faster, copies source directly)")


if __name__ == "__main__":
    main()
