# Run-PSPlaywright.ps1
# Discovery runner and entry point for the PSPlaywright e2e framework.
# Discovers all tests\Test-*.ps1 files, runs each in a shared or isolated session,
# aggregates results, and optionally writes a combined JUnit XML report.
#
# Usage:
#   .\Run-PSPlaywright.ps1
#   .\Run-PSPlaywright.ps1 -Filter "Diagram"
#   .\Run-PSPlaywright.ps1 -Browser firefox -Headless
#   .\Run-PSPlaywright.ps1 -Filter "Diagram" -JUnit -Verbose

[CmdletBinding()]
param(
    [string]$Filter      = "",          # substring filter on test file names
    [string]$Browser     = "",          # override: chromium, firefox, webkit
    [switch]$Headless,                  # run browser headlessly
    [switch]$JUnit,                     # write combined JUnit XML to test-results\
    [string]$ConfigPath  = "",          # path to psplaywright.config.json
    [switch]$IsolatedSessions           # create a fresh browser per test file (slower, more isolated)
)

$ErrorActionPreference = "Stop"
$runnerRoot = $PSScriptRoot

# ---- Locate config ----------------------------------------------------------
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $runnerRoot "psplaywright.config.json"
}
if (-not (Test-Path $ConfigPath)) {
    Write-Host "  [WARN] Config not found: $ConfigPath - using defaults" -ForegroundColor Yellow
    $ConfigPath = ""
}

# ---- Import framework -------------------------------------------------------
$modulePath = Join-Path $runnerRoot "PSPlaywright.psm1"
if (-not (Test-Path $modulePath)) {
    Write-Host "  [ERR] PSPlaywright.psm1 not found at: $modulePath" -ForegroundColor Red
    exit 1
}
Import-Module $modulePath -Force

# ---- Discover test files ----------------------------------------------------
$testsDir = Join-Path $runnerRoot "tests"
$allTests = Get-ChildItem -Path $testsDir -Filter "Test-*.ps1" -File |
    Sort-Object Name

if ($Filter) {
    $allTests = $allTests | Where-Object { $_.BaseName -like "*$Filter*" }
}

if (-not $allTests) {
    Write-Host "  [ERR] No test files matched filter '$Filter' in $testsDir" -ForegroundColor Red
    exit 1
}

# ---- Banner -----------------------------------------------------------------
Write-Host ""
Write-Host "  =================================================" -ForegroundColor Magenta
Write-Host "  PSPlaywright Test Runner" -ForegroundColor Magenta
Write-Host "  =================================================" -ForegroundColor Magenta
Write-Host ("  Config  : {0}" -f $(if ($ConfigPath) { $ConfigPath } else { "(defaults)" }))
Write-Host ("  Browser : {0}  Headless={1}" -f $(if ($Browser) { $Browser } else { "(config)" }), $Headless.IsPresent)
Write-Host ("  Filter  : {0}" -f $(if ($Filter) { $Filter } else { "(all)" }))
Write-Host ("  Found   : {0} test file(s)" -f $allTests.Count)
foreach ($f in $allTests) { Write-Host ("    - {0}" -f $f.Name) -ForegroundColor DarkGray }
Write-Host ""

# ---- Build context ----------------------------------------------------------
$ctxArgs = @{ ConfigPath = $ConfigPath }
if ($Browser)           { $ctxArgs.BrowserType = $Browser }
if ($Headless.IsPresent){ $ctxArgs.Headless     = $true }

$masterCtx = New-PWContext @ctxArgs
$masterCtx.StartTime = Get-Date

if (-not $IsolatedSessions.IsPresent) {
    Write-Host "  [RUN] Starting shared browser session..." -ForegroundColor Cyan
    Start-PWSession -Context $masterCtx
    $Global:PWTestContext = $masterCtx
}

# ---- Run each test file -----------------------------------------------------
$fileResults = @()
$runStart    = Get-Date

foreach ($testFile in $allTests) {
    Write-Host ""
    Write-Host ("  ---- {0} ----" -f $testFile.Name) -ForegroundColor Yellow

    if ($IsolatedSessions.IsPresent) {
        # Each file gets its own context + session
        $fileCtx = New-PWContext @ctxArgs
        Start-PWSession -Context $fileCtx
        $Global:PWTestContext = $fileCtx
    } else {
        $fileCtx = $masterCtx
        # Reset per-file state but keep browser/page alive
        $fileCtx.UploadId       = $null
        $fileCtx.AnalysisId     = $null
        $fileCtx.Tabs           = $null
        $fileCtx.CloudContext   = $null
        $fileCtx.AnalysisResult = $null
    }

    $fileBefore = $fileCtx.Results.Count
    $fileStart  = Get-Date

    try {
        . $testFile.FullName
    } catch {
        Write-Host ("  [ERR] Unhandled exception in {0}: {1}" -f $testFile.Name, $_.Exception.Message) -ForegroundColor Red
        $fileCtx.FailedTests++
        $fileCtx.Results.Add(@{
            Suite    = $testFile.BaseName
            Name     = "File-level exception"
            Status   = "FAIL"
            Error    = $_.Exception.Message
            Duration = 0
            Shot     = ""
        })
    }

    $fileDuration = [Math]::Round(((Get-Date) - $fileStart).TotalSeconds, 1)
    $newResults   = $fileCtx.Results | Select-Object -Skip $fileBefore
    $fp = ($newResults | Where-Object { $_.Status -eq "PASS" }).Count
    $ff = ($newResults | Where-Object { $_.Status -eq "FAIL" }).Count

    $fileResults += @{ File = $testFile.Name; Pass = $fp; Fail = $ff; Duration = $fileDuration }

    if ($IsolatedSessions.IsPresent) {
        Stop-PWSession -Context $fileCtx
        # Merge results into masterCtx for combined report
        foreach ($r in $fileCtx.Results) { $masterCtx.Results.Add($r) }
        $masterCtx.PassedTests  += $fileCtx.PassedTests
        $masterCtx.FailedTests  += $fileCtx.FailedTests
        $masterCtx.SkippedTests += $fileCtx.SkippedTests
    }
}

# ---- Teardown shared session ------------------------------------------------
if (-not $IsolatedSessions.IsPresent) {
    Stop-PWSession -Context $masterCtx
}
$Global:PWTestContext = $null

# ---- Combined report --------------------------------------------------------
$totalDuration = [Math]::Round(((Get-Date) - $runStart).TotalSeconds, 1)
$junitPath     = ""
if ($JUnit.IsPresent) {
    $ts        = Get-Date -Format "yyyyMMdd_HHmmss"
    $junitPath = Join-Path $masterCtx.Config.JUnitDir "psplaywright_${ts}.xml"
}

$allPassed = Write-PWReport -Context $masterCtx -JUnitPath $junitPath

# ---- Per-file summary -------------------------------------------------------
Write-Host "  File summary:" -ForegroundColor Cyan
foreach ($fr in $fileResults) {
    $col = if ($fr.Fail -gt 0) { "Red" } else { "Green" }
    Write-Host ("    {0,-45} pass={1}  fail={2}  ({3}s)" -f $fr.File, $fr.Pass, $fr.Fail, $fr.Duration) -ForegroundColor $col
}
Write-Host ""
Write-Host ("  Total wall time: {0}s" -f $totalDuration)
Write-Host ""

exit $(if ($allPassed) { 0 } else { 1 })
