# core/Report.ps1
# Describe/It test runner, result tracking, colored terminal output, JUnit XML export.
# Invoke-PWDescribe sets module-level current-suite state so Invoke-PWIt calls
# inside the body block do not need to pass -Context or -Suite explicitly.

# Module-level state set by Invoke-PWDescribe
$script:_PWCurrentSuite   = "Default"
$script:_PWCurrentContext = $null

function Invoke-PWDescribe {
    # Group Invoke-PWIt calls under a named suite. Prints a section header.
    # Usage: Invoke-PWDescribe "Suite Name" -Context $ctx { Invoke-PWIt ... }
    param(
        [string]$Suite,
        [scriptblock]$Body,
        [hashtable]$Context
    )

    $script:_PWCurrentSuite   = $Suite
    $script:_PWCurrentContext = $Context

    $line = "-" * [Math]::Min(($Suite.Length + 4), 60)
    Write-Host ""
    Write-Host "  $line" -ForegroundColor DarkGray
    Write-Host "  Suite: $Suite" -ForegroundColor Magenta
    Write-Host "  $line" -ForegroundColor DarkGray

    & $Body

    $script:_PWCurrentSuite   = "Default"
    $script:_PWCurrentContext = $null
}

function Invoke-PWIt {
    # Run a single test action. Catches failures, records result, auto-screenshots on FAIL.
    # Can be called inside Invoke-PWDescribe (context from module state) or standalone.
    param(
        [string]$Name,
        [scriptblock]$Action,
        [hashtable]$Context  = $null,
        [switch]$Skip
    )

    $ctx   = if ($Context) { $Context } else { $script:_PWCurrentContext }
    $suite = $script:_PWCurrentSuite

    if (-not $ctx) { throw "Invoke-PWIt '$Name': no Context provided and no active Describe block" }

    if ($Skip.IsPresent) {
        Write-Host ("    [ ] {0,-58} SKIP" -f $Name) -ForegroundColor DarkGray
        $ctx.SkippedTests++
        $ctx.Results.Add(@{
            Suite    = $suite
            Name     = $Name
            Status   = "SKIP"
            Error    = ""
            Duration = 0
            Shot     = ""
        })
        return
    }

    $start  = Get-Date
    $result = @{
        Suite    = $suite
        Name     = $Name
        Status   = "PASS"
        Error    = ""
        Duration = 0
        Shot     = ""
    }

    Write-Host -NoNewline ("    [ ] {0,-58}" -f $Name)

    try {
        & $Action
        $result.Status   = "PASS"
        $result.Duration = [Math]::Round(((Get-Date) - $start).TotalSeconds, 2)
        Write-Host ("`r    [+] {0,-58} {1,5}s" -f $Name, $result.Duration) -ForegroundColor Green
        $ctx.PassedTests++
    } catch {
        $result.Status   = "FAIL"
        $result.Error    = $_.Exception.Message
        $result.Duration = [Math]::Round(((Get-Date) - $start).TotalSeconds, 2)
        Write-Host ("`r    [x] {0,-58} {1,5}s" -f $Name, $result.Duration) -ForegroundColor Red
        Write-Host ("        ERROR: {0}" -f $result.Error) -ForegroundColor DarkRed
        $ctx.FailedTests++

        # Auto-screenshot
        if ($ctx.Page) {
            $ts   = Get-Date -Format "yyyyMMdd_HHmmss"
            $safe = $Name -replace '[^a-zA-Z0-9_-]', '_'
            $shot = Join-Path $ctx.Config.ScreenshotDir ("fail_${ts}_${safe}.png")
            try {
                Get-PlaywrightPageScreenshot -Path $shot -Page $ctx.Page
                $result.Shot = $shot
                Write-Host ("        Screenshot: {0}" -f $shot) -ForegroundColor DarkYellow
            } catch {}
        }
    }

    $ctx.Results.Add($result)
}

function Write-PWReport {
    # Print colored summary table and optionally write JUnit XML.
    # Call after all Invoke-PWDescribe blocks have finished.
    param(
        [hashtable]$Context,
        [string]$JUnitPath = ""
    )

    $results  = $Context.Results
    $total    = $results.Count
    $passed   = ($results | Where-Object { $_.Status -eq "PASS" }).Count
    $failed   = ($results | Where-Object { $_.Status -eq "FAIL" }).Count
    $skipped  = ($results | Where-Object { $_.Status -eq "SKIP" }).Count
    $duration = [Math]::Round(((Get-Date) - $Context.StartTime).TotalSeconds, 1)

    Write-Host ""
    Write-Host "  =================================================" -ForegroundColor Magenta
    Write-Host ("  RESULTS  {0}" -f (Get-Date -Format "HH:mm:ss")) -ForegroundColor Magenta
    Write-Host "  =================================================" -ForegroundColor Magenta

    # Per-suite breakdown
    $suites = $results | Select-Object -ExpandProperty Suite -Unique
    foreach ($suite in $suites) {
        $sr = $results | Where-Object { $_.Suite -eq $suite }
        $sp = ($sr | Where-Object { $_.Status -eq "PASS" }).Count
        $sf = ($sr | Where-Object { $_.Status -eq "FAIL" }).Count
        $ss = ($sr | Where-Object { $_.Status -eq "SKIP" }).Count
        $suiteColor = if ($sf -gt 0) { "Yellow" } else { "Green" }
        Write-Host ""
        Write-Host ("  {0}  [{1}p {2}f {3}s]" -f $suite, $sp, $sf, $ss) -ForegroundColor $suiteColor

        foreach ($r in $sr) {
            switch ($r.Status) {
                "PASS" {
                    Write-Host ("    [+] {0,-56} {1,5}s" -f $r.Name, $r.Duration) -ForegroundColor Green
                }
                "FAIL" {
                    Write-Host ("    [x] {0,-56} {1,5}s" -f $r.Name, $r.Duration) -ForegroundColor Red
                    if ($r.Error) {
                        Write-Host ("        {0}" -f $r.Error) -ForegroundColor DarkRed
                    }
                    if ($r.Shot) {
                        Write-Host ("        Screenshot: {0}" -f $r.Shot) -ForegroundColor DarkYellow
                    }
                }
                "SKIP" {
                    Write-Host ("    [-] {0,-56}  SKIP" -f $r.Name) -ForegroundColor DarkGray
                }
            }
        }
    }

    Write-Host ""
    $summaryColor = if ($failed -gt 0) { "Red" } else { "Green" }
    Write-Host ("  Total: {0}  |  Pass: {1}  |  Fail: {2}  |  Skip: {3}  |  Time: {4}s" -f $total, $passed, $failed, $skipped, $duration) -ForegroundColor $summaryColor
    Write-Host ""

    # JUnit XML
    if ($JUnitPath) {
        _Write-PWJUnitXml -Context $Context -Path $JUnitPath -Total $total -Failed $failed -Duration $duration
        OK "JUnit XML: $JUnitPath"
    }

    # Return exit-code-ready boolean
    return ($failed -eq 0)
}

function _Write-PWJUnitXml {
    param([hashtable]$Context, [string]$Path, [int]$Total, [int]$Failed, [double]$Duration)

    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }

    function _XmlEsc { param([string]$s); $s -replace '&','&amp;' -replace '<','&lt;' -replace '>','&gt;' -replace '"','&quot;' }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('<?xml version="1.0" encoding="UTF-8"?>')
    [void]$sb.AppendLine("<testsuites name=`"PSPlaywright`" tests=`"$Total`" failures=`"$Failed`" time=`"$Duration`">")

    $suites = $Context.Results | Select-Object -ExpandProperty Suite -Unique
    foreach ($suite in $suites) {
        $sr   = $Context.Results | Where-Object { $_.Suite -eq $suite }
        $sc   = $sr.Count
        $sf   = ($sr | Where-Object { $_.Status -eq "FAIL" }).Count
        $st   = [Math]::Round(($sr | Measure-Object -Property Duration -Sum).Sum, 3)
        $sn   = _XmlEsc $suite
        [void]$sb.AppendLine("  <testsuite name=`"$sn`" tests=`"$sc`" failures=`"$sf`" time=`"$st`">")

        foreach ($r in $sr) {
            $rn = _XmlEsc $r.Name
            $sname = _XmlEsc $suite
            switch ($r.Status) {
                "PASS" {
                    [void]$sb.AppendLine("    <testcase name=`"$rn`" classname=`"$sname`" time=`"$($r.Duration)`"/>")
                }
                "FAIL" {
                    $em = _XmlEsc $r.Error
                    [void]$sb.AppendLine("    <testcase name=`"$rn`" classname=`"$sname`" time=`"$($r.Duration)`">")
                    [void]$sb.AppendLine("      <failure message=`"$em`">$em</failure>")
                    [void]$sb.AppendLine("    </testcase>")
                }
                "SKIP" {
                    [void]$sb.AppendLine("    <testcase name=`"$rn`" classname=`"$sname`" time=`"0`">")
                    [void]$sb.AppendLine("      <skipped/>")
                    [void]$sb.AppendLine("    </testcase>")
                }
            }
        }
        [void]$sb.AppendLine("  </testsuite>")
    }
    [void]$sb.AppendLine("</testsuites>")

    [System.IO.File]::WriteAllText($Path, $sb.ToString(), [System.Text.Encoding]::UTF8)
}

# Private alias used in _Write-PWJUnitXml scope
function OK { param($m); Write-Host "  [OK] $m" -ForegroundColor Green }
