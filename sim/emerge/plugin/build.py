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

    # Remove the plugin from KiCad
    python build.py --uninstall

    # Specify KiCad version or plugin version
    python build.py --kicad-version 10.0 --version 1.1.0

Deployment — single location
-----------------------------
The plugin lives in exactly ONE place on disk (the KiCad PCM 3rdparty directory).
Installing it in BOTH 3rdparty AND scripting/plugins causes double-registration
and makes the toolbar icon disappear.  Never copy the plugin to:
    %APPDATA%/kicad/<ver>/scripting/plugins/

Correct install location (Windows):
    %OneDrive%/Simulation tools/KiCad/<version>/3rdparty/plugins/com_github_<owner>_emerge_fem/

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
import subprocess
import shutil
import sys
import zipfile

# ── paths ─────────────────────────────────────────────────────────────────────
THIS_DIR   = pathlib.Path(__file__).parent.resolve()  # sim/emerge/plugin/
EMERGE_DIR = THIS_DIR.parent                          # sim/emerge/
DIST_DIR   = THIS_DIR / "build"   # output dir (compatible with central KiCAD-Plugin build.py)
STAGE_DIR  = DIST_DIR / "stage"

# Folder name of the installed plugin. Set EMERGE_PLUGIN_NAME to keep the name of an existing installation.
PLUGIN_NAME = os.environ.get("EMERGE_PLUGIN_NAME", "com_github_example_emerge_fem")

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
    "gerber_builder.py",
    "passive_modeler.py",
    "gerber_exporter.py",
    "kicad_reader.py",
    "python_bridge.py",
]

# emerge_pipeline.py is intentionally excluded — it is the standalone CLI,
# not part of the KiCad plugin runtime.


# Files that must NOT be in the deployed plugin directory.
# emerge_pipeline.py is the standalone CLI — not part of the KiCad plugin.
_STALE_FILES = [
    "emerge_pipeline.py",
]

# The old duplicate install location — must never exist alongside 3rdparty.
def _appdata_scripting_dir(kicad_version: str) -> pathlib.Path:
    return (pathlib.Path(os.environ.get("APPDATA", "")) /
            "kicad" / kicad_version / "scripting" / "plugins" / "emerge_plugin")


def _kicad_plugin_dir(kicad_version: str) -> pathlib.Path:
    """Return the KiCad 3rdparty/plugins directory for this machine."""
    if platform.system() == "Windows":
        onedrive = (os.environ.get("OneDrive") or
                    str(pathlib.Path.home() / "OneDrive"))
        return (pathlib.Path(onedrive) / "Simulation tools" / "KiCad" /
                kicad_version / "3rdparty" / "plugins")
    # Linux / macOS fallback
    return pathlib.Path.home() / ".local" / "share" / "kicad" / kicad_version / "3rdparty" / "plugins"


def _detect_kicad_versions() -> list[str]:
    """Return detected KiCad version folders, newest first (e.g. ['10.0','9.0'])."""
    versions: set[str] = set()

    # 1) OneDrive project-local KiCad folders
    onedrive = (os.environ.get("OneDrive") or
                str(pathlib.Path.home() / "OneDrive"))
    root = pathlib.Path(onedrive) / "Simulation tools" / "KiCad"
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and d.name.count(".") == 1:
                versions.add(d.name)

    # 2) Program Files KiCad installs
    pf = pathlib.Path(r"C:\Program Files\KiCad")
    if pf.is_dir():
        for d in pf.iterdir():
            if d.is_dir() and d.name.count(".") == 1:
                versions.add(d.name)

    def _vkey(s: str):
        try:
            return tuple(int(x) for x in s.split("."))
        except Exception:
            return (0,)

    out = sorted(versions, key=_vkey, reverse=True)
    if out:
        return out
    # Fallback when nothing can be detected from filesystem.
    return ["10.0", "9.0"]


def _resolve_kicad_version(kicad_version: str) -> str:
    """
    Resolve KiCad version option.
    - explicit value like '9.0' or '10.0' is used as-is
    - 'auto' picks newest detected version
    """
    val = (kicad_version or "auto").strip().lower()
    if val != "auto":
        return kicad_version
    detected = _detect_kicad_versions()
    return detected[0] if detected else "10.0"


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


def _cleanup_duplicates(kicad_version: str):
    """
    Remove the old AppData scripting/plugins duplicate if it exists.
    Having the plugin in both 3rdparty and scripting/plugins causes
    double-registration and makes the toolbar icon disappear.
    """
    dup = _appdata_scripting_dir(kicad_version)
    if dup.exists():
        try:
            shutil.rmtree(dup)
            print(f"  Removed duplicate install: {dup}")
        except Exception as exc:
            print(f"  WARNING: could not remove duplicate {dup}: {exc}")
    else:
        print(f"  No duplicate found at: {dup}")


def _remove_stale_files(dest: pathlib.Path):
    """Delete files that must not be in the deployed plugin directory."""
    for name in _STALE_FILES:
        stale = dest / name
        if stale.exists():
            stale.unlink()
            print(f"  Removed stale file: {name}")


def _uninstall(kicad_version: str):
    """Remove the plugin from the KiCad 3rdparty plugins directory."""
    plugins_root = _kicad_plugin_dir(kicad_version)
    dest = plugins_root / PLUGIN_NAME
    if dest.exists():
        shutil.rmtree(dest)
        print(f"  Uninstalled: {dest}")
    else:
        print(f"  Not installed at: {dest}")
    _cleanup_duplicates(kicad_version)


def _dev_deploy(kicad_version: str):
    """
    Copy source files directly to KiCad's plugin folder — no ZIP needed.
    Faster than build+deploy for development iteration.
    Also removes any duplicate AppData install and stale files.
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

    # Remove any stale files left over from old versions
    _remove_stale_files(dest)

    # Remove duplicate AppData install (causes double-registration / missing icon)
    _cleanup_duplicates(kicad_version)

    # Clear stale .pyc so KiCad picks up fresh source (may be locked if KiCad is open)
    pycache = dest / "__pycache__"
    if pycache.exists():
        try:
            shutil.rmtree(pycache)
            print("  Cleared __pycache__")
        except PermissionError:
            print("  __pycache__ locked (KiCad running?) — skipped; restart KiCad to reload")

    return True


def _run_headless_debug() -> bool:
    """
    Run the headless pipeline test after sync/deploy.
    Uses plugin emerge_config.toml and forces viewer windows OFF.
    """
    headless = EMERGE_DIR / "headless_emerge_test.py"
    if not headless.exists():
        print(f"  ERROR: headless test script not found: {headless}")
        return False

    cmd = [
        sys.executable,
        str(headless),
        "--run",
        "--debug",
        "--config", str(THIS_DIR / "emerge_config.toml"),
        "--force-no-viewers",
    ]
    print("\nRunning headless debug check ...")
    print("  " + " ".join(cmd))

    try:
        rc = subprocess.run(cmd, cwd=str(EMERGE_DIR)).returncode
    except Exception as exc:
        print(f"  ERROR: failed to launch headless debug: {exc}")
        return False

    if rc != 0:
        print(f"  ERROR: headless debug failed (exit code {rc})")
        return False

    print("  Headless debug: PASS")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Package and/or deploy the EMerge KiCad plugin")
    parser.add_argument("--zip",              action="store_true",
                        help="Build ZIP only (used by central KiCAD-Plugin build.py)")
    parser.add_argument("--deploy",           action="store_true",
                        help="Build ZIP then install into KiCad plugins directory")
    parser.add_argument("--dev-deploy",       action="store_true",
                        help="Copy source files directly to KiCad (fast dev iteration)")
    parser.add_argument("--uninstall",        action="store_true",
                        help="Remove the plugin from KiCad plugins directory")
    parser.add_argument("--clean-duplicates", action="store_true",
                        help="Remove duplicate AppData scripting/plugins install if present")
    parser.add_argument("--kicad-version",    default="auto",
                        help="KiCad version string (e.g. 9.0, 10.0) or 'auto' (default)")
    parser.add_argument("--version",          default=None,
                        help="Plugin version string (default: read from metadata.json)")
    parser.add_argument("--headless-debug",   action="store_true",
                        help="Run headless debug validation after deploy/dev-deploy")
    parser.add_argument("--no-headless-debug", action="store_true",
                        help="Skip headless debug validation after deploy/dev-deploy")
    args = parser.parse_args()

    version = args.version or _read_version_from_metadata()
    kicad_version = _resolve_kicad_version(args.kicad_version)

    # ── uninstall ────────────────────────────────────────────────────────────
    if args.uninstall:
        print(f"\nEMerge uninstall — KiCad {kicad_version}")
        print("=" * 50)
        _uninstall(kicad_version)
        return

    # ── clean duplicates only ────────────────────────────────────────────────
    if args.clean_duplicates:
        print(f"\nEMerge clean duplicates — KiCad {kicad_version}")
        print("=" * 50)
        _cleanup_duplicates(kicad_version)
        return

    # ── dev-deploy: copy from source, skip ZIP ───────────────────────────────
    if args.dev_deploy:
        print(f"\nEMerge dev-deploy → KiCad {kicad_version}")
        print("=" * 50)
        ok = _dev_deploy(kicad_version)
        if ok:
            run_headless = args.headless_debug or not args.no_headless_debug
            if run_headless:
                if not _run_headless_debug():
                    raise SystemExit(1)
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
        print(f"\nDeploying from ZIP → KiCad {kicad_version} ...")
        ok = _deploy_from_zip(zip_path, kicad_version)
        if ok:
            run_headless = args.headless_debug or not args.no_headless_debug
            if run_headless:
                if not _run_headless_debug():
                    raise SystemExit(1)
            print("\nDone. In KiCad: Tools → External Plugins → Refresh Plugins")
    else:
        print("\nDone. To install:")
        print(f"  python build.py --deploy")
        print(f"  — or —")
        print(f"  python build.py --dev-deploy   (faster, copies source directly)")


if __name__ == "__main__":
    main()
