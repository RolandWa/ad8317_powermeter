<#
.SYNOPSIS
    Run the EMerge plugin test suite.

.DESCRIPTION
    Runs pytest from the project root so that module imports resolve correctly.
    No KiCad or EMerge installation required -- all EMerge API calls are mocked.

.PARAMETER Coverage
    Add --cov=sim/emerge to generate a coverage report.

.PARAMETER Filter
    Optional pytest -k filter expression.  Named shortcuts:
        kicad       → kicad_reader tests only
        passives    → passive component parser + modeler tests
        runner      → emerge_runner tests (model builder, solver, reporter)
        values      → parse_component_value tests only
        e2e         → end-to-end chain test only

.EXAMPLE
    # Run all tests
    .\run_tests.ps1

    # Run only passive component tests
    .\run_tests.ps1 -Filter passives

    # Run value-parser tests with coverage
    .\run_tests.ps1 -Filter values -Coverage
#>

param(
    [string]$Filter   = "",
    [switch]$Coverage
)

# Named shortcut aliases → pytest -k expressions
$filterAliases = @{
    "kicad"    = "test_kicad_reader"
    "passives" = "TestReadPassiveComponents or TestPassiveElementModeler"
    "runner"   = "test_emerge_runner"
    "values"   = "TestParseComponentValue"
    "e2e"      = "TestEndToEnd"
}

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..\..")

$pytestArgs = @(
    "sim/emerge/tests/",
    "-v",
    "--tb=short"
)

$resolvedFilter = if ($filterAliases.ContainsKey($Filter)) { $filterAliases[$Filter] } else { $Filter }

if ($resolvedFilter) {
    $pytestArgs += @("-k", $resolvedFilter)
}

if ($Coverage) {
    $pytestArgs += @("--cov=sim/emerge", "--cov-report=term-missing")
}

Write-Host "EMerge test suite"
Write-Host "Root: $projectRoot"
if ($resolvedFilter) { Write-Host "Filter: $resolvedFilter" }
Write-Host ""

Push-Location $projectRoot
try {
    python -m pytest @pytestArgs
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $exit
