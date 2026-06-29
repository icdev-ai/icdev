# core/Browser.ps1
# Browser lifecycle, shared context factory, retry helper, and async task spinner.
# All e2e tests share one context hashtable created by New-PWContext.

function New-PWContext {
    # Create the shared test context. Pass -ConfigPath to load psplaywright.config.json,
    # or pass individual overrides. The context is a hashtable mutated by every helper.
    param(
        [string]$ConfigPath   = "",
        [string]$BaseUrl      = "",
        [string]$BrowserType  = "",
        [switch]$Headless,
        [int]$TimeoutMs       = 0
    )

    # Defaults
    $cfg = @{
        BaseUrl            = "http://localhost:5050"
        BrowserType        = "chromium"
        Headless           = $false
        TimeoutMs          = 10000
        AnalysisTimeoutSec = 180
        RetryTimes         = 3
        RetryDelayMs       = 500
        ScreenshotDir      = "C:\AI\ICDev\playwright\screenshots"
        ExportDir          = "C:\AI\ICDev\data\ndc_uploads\exports"
        BrowsersPath       = "C:\Users\schuo\AppData\Local\ms-playwright"
        JUnitDir           = "C:\AI\ICDev\tests\psplaywright\test-results"
        DefaultIndustry    = "dod_il4"
        DefaultDiagramPath = ""
    }

    # Overlay from config file
    if ($ConfigPath -and (Test-Path $ConfigPath)) {
        $json = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        foreach ($prop in $json.PSObject.Properties) {
            $cfg[$prop.Name] = $prop.Value
        }
    }

    # CLI overrides
    if ($BaseUrl)              { $cfg.BaseUrl      = $BaseUrl }
    if ($BrowserType)          { $cfg.BrowserType  = $BrowserType }
    if ($Headless.IsPresent)   { $cfg.Headless     = $true }
    if ($TimeoutMs -gt 0)      { $cfg.TimeoutMs    = $TimeoutMs }

    return @{
        Config         = $cfg
        Browser        = $null
        Page           = $null
        HttpClient     = $null
        Results        = [System.Collections.Generic.List[hashtable]]::new()
        StartTime      = Get-Date
        # Test state (mutated by page objects and Invoke-PWIt)
        UploadId       = $null
        AnalysisId     = $null
        Tabs           = $null
        CloudContext   = $null
        AnalysisResult = $null
        # Counters
        PassedTests    = 0
        FailedTests    = 0
        SkippedTests   = 0
    }
}

function Start-PWSession {
    # Initialise Playwright, launch browser, open blank page, create HttpClient.
    param([hashtable]$Context)

    $cfg = $Context.Config
    $env:PLAYWRIGHT_BROWSERS_PATH = $cfg.BrowsersPath

    # Ensure output directories exist
    foreach ($dir in @($cfg.ScreenshotDir, $cfg.ExportDir, $cfg.JUnitDir)) {
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    }

    # Shared HttpClient
    $timeoutSec = [Math]::Max([int]$cfg.AnalysisTimeoutSec, 240)
    $Context.HttpClient = New-PWHttpClient -TimeoutSec $timeoutSec

    Write-Host "  [PW] Importing PSPlaywright module..." -ForegroundColor DarkCyan
    Import-Module PSPlaywright -Force -ErrorAction Stop

    Write-Host "  [PW] Starting Playwright runtime..." -ForegroundColor DarkCyan
    Start-Playwright

    $bargs = @{ BrowserType = $cfg.BrowserType }
    if ($cfg.Headless) { $bargs.Headless = $true }

    Write-Host "  [PW] Launching $($cfg.BrowserType) (headless=$($cfg.Headless))..." -ForegroundColor DarkCyan
    $Context.Browser = Start-PlaywrightBrowser @bargs

    Write-Host "  [PW] Opening blank page..." -ForegroundColor DarkCyan
    $Context.Page = Open-PlaywrightPage -Browser $Context.Browser

    Write-Host "  [PW] Session ready." -ForegroundColor Green
}

function Stop-PWSession {
    # Tear down browser and HttpClient. Auto-screenshots final state on any failures.
    param([hashtable]$Context)

    if ($Context.FailedTests -gt 0 -and $Context.Page) {
        $shot = Join-Path $Context.Config.ScreenshotDir ("session_failure_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".png")
        try {
            Get-PlaywrightPageScreenshot -Path $shot -Page $Context.Page
            Write-Host "  [PW] Failure screenshot: $shot" -ForegroundColor Yellow
        } catch {}
    }

    if ($Context.HttpClient) {
        try { $Context.HttpClient.Dispose() } catch {}
        $Context.HttpClient = $null
    }

    try { Stop-Playwright } catch {}

    Write-Host "  [PW] Session stopped." -ForegroundColor DarkCyan
}

function Invoke-PWRetry {
    # Run $Action up to $Times, doubling delay after each failure (exponential backoff).
    param(
        [scriptblock]$Action,
        [int]$Times    = 3,
        [int]$DelayMs  = 500,
        [string]$Label = "action"
    )

    $attempt  = 0
    $lastErr  = $null
    $delay    = $DelayMs

    while ($attempt -lt $Times) {
        try {
            return (& $Action)
        } catch {
            $attempt++
            $lastErr = $_
            if ($attempt -lt $Times) {
                Write-Host ("  [RETRY] {0} (attempt {1}/{2}): {3}" -f $Label, $attempt, $Times, $_.Exception.Message) -ForegroundColor Yellow
                Start-Sleep -Milliseconds $delay
                $delay = $delay * 2
            }
        }
    }

    throw ("Max retries ({0}) exceeded for '{1}': {2}" -f $Times, $Label, $lastErr.Exception.Message)
}

function Wait-PWTask {
    # Animate a spinner while a System.Threading.Tasks.Task runs. Returns Task.Result.
    # Throws the inner exception on fault so callers get a clean error.
    param(
        [System.Threading.Tasks.Task]$Task,
        [string]$Label = "working"
    )

    $spin  = @('|', '/', '-', '\')
    $i     = 0
    $start = Get-Date

    while (-not $Task.IsCompleted) {
        $elapsed = [int]((Get-Date) - $start).TotalSeconds
        Write-Host -NoNewline ("`r    {0}  {1}  ({2}s)...   " -f $spin[$i % 4], $Label, $elapsed)
        Start-Sleep -Milliseconds 250
        $i++
    }

    $elapsed = [int]((Get-Date) - $start).TotalSeconds
    Write-Host ("`r    Done in {0}s                                         " -f $elapsed)

    if ($Task.IsFaulted) {
        $inner = $Task.Exception.InnerException
        if ($inner) { throw $inner } else { throw $Task.Exception }
    }

    return $Task.Result
}

function Get-PWScreenshotPath {
    # Build a timestamped screenshot path under ScreenshotDir.
    param([hashtable]$Context, [string]$Name)
    $ts   = Get-Date -Format "yyyyMMdd_HHmmss"
    $safe = $Name -replace '[^a-zA-Z0-9_-]', '_'
    return Join-Path $Context.Config.ScreenshotDir "${ts}_${safe}.png"
}

function Save-PWScreenshot {
    # Take a screenshot and return the path. Silent on error.
    param([hashtable]$Context, [string]$Name)
    try {
        $path = Get-PWScreenshotPath -Context $Context -Name $Name
        Get-PlaywrightPageScreenshot -Path $path -Page $Context.Page
        return $path
    } catch {
        return ""
    }
}
