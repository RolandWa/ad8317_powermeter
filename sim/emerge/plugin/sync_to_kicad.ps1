<#
.SYNOPSIS
    Dev-deploy the EMerge plugin to KiCad's 3rdparty/plugins folder.

.DESCRIPTION
    Delegates to build.py --dev-deploy which:
      • Copies source files from sim/emerge/plugin/ and sim/emerge/ to the
        single correct install location:
            %OneDrive%\Simulation tools\KiCad\<ver>\3rdparty\plugins\com_github_<github-user>_emerge_fem\
      • Removes any stale files (e.g. emerge_pipeline.py) from the install dir.
      • Removes any duplicate install from %APPDATA%\kicad\<ver>\scripting\plugins\
        (double registration causes the toolbar icon to disappear).
      • Clears __pycache__ so KiCad picks up fresh source on next start.

    NOTE: Close KiCad before syncing — the __pycache__ folder is locked while
    KiCad is running.  If KiCad is open, use Tools → External Plugins →
    Refresh Plugins after the sync instead.

.PARAMETER KiCadVersion
    KiCad version folder name (default: 9.0)

.PARAMETER Uninstall
    Remove the plugin from KiCad's plugins directory and clean up duplicates.

.EXAMPLE
    # Install / update
    .\sync_to_kicad.ps1

    # Specify KiCad version
    .\sync_to_kicad.ps1 -KiCadVersion "9.0"

    # Remove plugin
    .\sync_to_kicad.ps1 -Uninstall
#>

param(
    [string]$KiCadVersion = "9.0",
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot   # sim/emerge/plugin/
$buildScript = Join-Path $scriptDir "build.py"

if (-not (Test-Path $buildScript)) {
    Write-Error "build.py not found at $buildScript"
    exit 1
}

Write-Host "EMerge plugin sync (KiCad $KiCadVersion)"
Write-Host ""

if ($Uninstall) {
    python $buildScript --uninstall --kicad-version $KiCadVersion
} else {
    python $buildScript --dev-deploy --kicad-version $KiCadVersion
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "build.py exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
