<#
.SYNOPSIS
    Fast dev-deploy of the EMerge plugin to KiCad's 3rdparty/plugins folder.

.DESCRIPTION
    Delegates to build.py --dev-deploy which copies source files from:
        sim/emerge/plugin/    -> plugin entry point files
        sim/emerge/           -> engine modules

    The list of deployed files is maintained in build.py (single source).
    No ZIP is created -- use  python build.py --deploy  for a PCM ZIP install.

.PARAMETER KiCadVersion
    KiCad version folder name (default: 9.0)

.PARAMETER Uninstall
    Remove the plugin from KiCad's plugins directory.

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

# -- uninstall -----------------------------------------------------------------
if ($Uninstall) {
    $onedrive = if ($env:OneDrive) { $env:OneDrive }
                else { Join-Path $env:USERPROFILE "<cloud-folder>" }
    $dest = Join-Path $onedrive "Simulation tools\KiCad\$KiCadVersion\3rdparty\plugins\com_github_<github-user>_emerge_fem"
    if (Test-Path $dest) {
        Remove-Item $dest -Recurse -Force
        Write-Host "Uninstalled: $dest"
    } else {
        Write-Host "Plugin not found at $dest -- nothing to remove."
    }
    exit 0
}

# -- dev-deploy via build.py ---------------------------------------------------
$buildScript = Join-Path $scriptDir "build.py"
if (-not (Test-Path $buildScript)) {
    Write-Error "build.py not found at $buildScript"
    exit 1
}

Write-Host "EMerge plugin dev-deploy (KiCad $KiCadVersion)"
Write-Host ""

python $buildScript --dev-deploy --kicad-version $KiCadVersion

if ($LASTEXITCODE -ne 0) {
    Write-Error "build.py exited with code $LASTEXITCODE"
    exit $LASTEXITCODE
}
