<#
.SYNOPSIS
    Dev-deploy the EMerge plugin to installed KiCad versions.

.DESCRIPTION
    Calls build.py for one or more KiCad versions.
    - If -KiCadVersion is provided, only that version is processed.
    - Otherwise, the script auto-detects versions under:
      %OneDrive%\Simulation tools\KiCad\
      and falls back to 9.0 and 10.0 if none are found.

.PARAMETER KiCadVersion
    KiCad version folder name (for example: "9.0" or "10.0").

.PARAMETER Uninstall
    Remove plugin instead of deploying it.
#>

param(
    [string]$KiCadVersion = "",
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = $PSScriptRoot
$buildScript = Join-Path $scriptDir "build.py"

if (-not (Test-Path $buildScript)) {
    Write-Error "build.py not found at $buildScript"
    exit 1
}

if ($KiCadVersion -ne "") {
    $versions = @($KiCadVersion)
} else {
    $oneDrive = $env:OneDrive
    if (-not $oneDrive) {
        $oneDrive = Join-Path $env:USERPROFILE "<cloud-folder>"
    }

    $kicadRoot = Join-Path $oneDrive "Simulation tools\KiCad"
    if (Test-Path $kicadRoot) {
        $versions = Get-ChildItem -Path $kicadRoot -Directory |
            Where-Object { $_.Name -match '^\d+\.\d+$' } |
            Sort-Object Name |
            ForEach-Object { $_.Name }
    } else {
        $versions = @()
    }

    if ($versions.Count -eq 0) {
        $versions = @("9.0", "10.0")
        Write-Host "KiCad root not found at $kicadRoot. Trying default versions: $($versions -join ', ')"
    }
}

Write-Host "EMerge plugin sync"
Write-Host "Versions to process: $($versions -join ', ')"
Write-Host ""

$anyFailed = $false

foreach ($ver in $versions) {
    Write-Host "--- KiCad $ver ---"
    if ($Uninstall) {
        python $buildScript --uninstall --kicad-version $ver
    } else {
        python $buildScript --dev-deploy --kicad-version $ver
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: build.py exited with code $LASTEXITCODE for KiCad $ver"
        $anyFailed = $true
    }

    Write-Host ""
}

if ($anyFailed) {
    Write-Error "One or more versions failed. See output above."
    exit 1
}
