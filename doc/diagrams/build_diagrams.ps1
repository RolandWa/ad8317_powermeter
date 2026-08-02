<#
.SYNOPSIS
    Render all PlantUML (.puml) and Mermaid (.mmd) diagram sources to PNG/SVG.

.DESCRIPTION
    Scans doc/diagrams/src/ for *.puml and *.mmd files and renders them to
    doc/diagrams/out/ using:
      PlantUML 1.2025.0  — doc/tools/plantuml.jar  (requires Java)
      Mermaid CLI 11.x   — mmdc  (requires Node.js, npm install -g @mermaid-js/mermaid-cli)

    Output files keep the same base name with .png or .svg extension.
    Re-renders only files newer than their output (unless -Force is set).

.PARAMETER Force
    Re-render all diagrams even if output is up to date.

.PARAMETER Format
    Output format: png (default) or svg.

.EXAMPLE
    .\build_diagrams.ps1
    .\build_diagrams.ps1 -Force
    .\build_diagrams.ps1 -Format svg

.NOTES
    Java location : C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot\bin\java.exe
    PlantUML jar  : doc\tools\plantuml.jar
    mmdc          : C:\Users\<user>\AppData\Roaming\npm\mmdc.ps1
#>

param(
    [switch]$Force,
    [ValidateSet("png","svg")]
    [string]$Format = "png"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── paths ──────────────────────────────────────────────────────────────────────
$scriptDir   = $PSScriptRoot
$srcDir      = Join-Path $scriptDir "src"
$outDir      = Join-Path $scriptDir "out"
$toolsDir    = Join-Path $scriptDir "..\tools"
$plantumlJar = Join-Path $toolsDir "plantuml.jar"

$javaExe = "C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot\bin\java.exe"
$mmdcExe = "$env:APPDATA\npm\mmdc.ps1"   # installed via: npm install -g @mermaid-js/mermaid-cli

# Add Node.js to PATH so mmdc can find its runtime
$env:PATH = "C:\Program Files\nodejs;$env:APPDATA\npm;$env:PATH"

# ── validate tools ─────────────────────────────────────────────────────────────
if (-not (Test-Path $javaExe)) {
    Write-Error "Java not found at $javaExe`nInstall: winget install Microsoft.OpenJDK.21"
}
if (-not (Test-Path $plantumlJar)) {
    Write-Error "plantuml.jar not found at $plantumlJar`nDownload from https://plantuml.com/download"
}
$mmdcCmd = Get-Command mmdc -ErrorAction SilentlyContinue
if (-not $mmdcCmd) {
    Write-Error "mmdc not found — install: npm install -g @mermaid-js/mermaid-cli"
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# ── helpers ────────────────────────────────────────────────────────────────────
function NeedsRebuild($src, $out) {
    if ($Force) { return $true }
    if (-not (Test-Path $out)) { return $true }
    return (Get-Item $src).LastWriteTime -gt (Get-Item $out).LastWriteTime
}

$built = 0; $skipped = 0; $failed = 0

# ── PlantUML (.puml) ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "PlantUML diagrams (.puml → .$Format)" -ForegroundColor Cyan

Get-ChildItem -Path $srcDir -Filter "*.puml" -ErrorAction SilentlyContinue | ForEach-Object {
    $src = $_.FullName
    $out = Join-Path $outDir ($_.BaseName + ".$Format")

    if (-not (NeedsRebuild $src $out)) {
        Write-Host "  SKIP  $($_.Name)"
        $skipped++
        return
    }

    Write-Host "  BUILD $($_.Name) → $($_.BaseName).$Format"
    try {
        $fmt = if ($Format -eq "svg") { "-tsvg" } else { "-tpng" }
        & $javaExe -jar $plantumlJar $fmt -o $outDir $src 2>&1 | ForEach-Object { "         $_" }
        if ($LASTEXITCODE -ne 0) { throw "plantuml exited $LASTEXITCODE" }
        $built++
    } catch {
        Write-Host "  FAIL  $($_.Name): $_" -ForegroundColor Red
        $failed++
    }
}

# ── Mermaid (.mmd) ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Mermaid diagrams (.mmd → .$Format)" -ForegroundColor Cyan

Get-ChildItem -Path $srcDir -Filter "*.mmd" -ErrorAction SilentlyContinue | ForEach-Object {
    $src = $_.FullName
    $out = Join-Path $outDir ($_.BaseName + ".$Format")

    if (-not (NeedsRebuild $src $out)) {
        Write-Host "  SKIP  $($_.Name)"
        $skipped++
        return
    }

    Write-Host "  BUILD $($_.Name) → $($_.BaseName).$Format"
    try {
        & mmdc -i $src -o $out -b white 2>&1 | ForEach-Object { "         $_" }
        if ($LASTEXITCODE -ne 0) { throw "mmdc exited $LASTEXITCODE" }
        $built++
    } catch {
        Write-Host "  FAIL  $($_.Name): $_" -ForegroundColor Red
        $failed++
    }
}

# ── summary ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Done — built: $built  skipped: $skipped  failed: $failed" -ForegroundColor $(if($failed){"Red"}else{"Green"})
if ($failed -gt 0) { exit 1 }
