# PSPlaywright.psm1
# Module entry point. Dot-sources all core helpers and page objects in dependency order.
# Import: Import-Module C:\AI\ICDev\tests\psplaywright\PSPlaywright.psm1 -Force

$coreDir  = Join-Path $PSScriptRoot "core"
$pagesDir = Join-Path $PSScriptRoot "pages"

# Core: Http first (Wait-PWTask used by Browser), then Browser, Assert, Report
. (Join-Path $coreDir  "Http.ps1")
. (Join-Path $coreDir  "Browser.ps1")
. (Join-Path $coreDir  "Assert.ps1")
. (Join-Path $coreDir  "Report.ps1")

# Pages
. (Join-Path $pagesDir "BasePage.ps1")
. (Join-Path $pagesDir "DiagramAnalysisPage.ps1")

Export-ModuleMember -Function @(
    # Browser lifecycle
    'New-PWContext', 'Start-PWSession', 'Stop-PWSession',
    'Invoke-PWRetry', 'Wait-PWTask',
    # HTTP helpers
    'New-PWHttpClient', 'Invoke-PWMultipartUpload', 'Invoke-PWJSON', 'Save-PWBinary',
    # Assertions
    'Assert-PWVisible', 'Assert-PWNotVisible', 'Assert-PWText', 'Assert-PWContainsText',
    'Assert-PWCount', 'Assert-PWMinCount', 'Assert-PWNotExist',
    'Assert-PWUrl', 'Assert-PWTitle', 'Assert-PWValue', 'Assert-PWTrue',
    # Runner
    'Invoke-PWDescribe', 'Invoke-PWIt', 'Write-PWReport',
    # Pages
    'New-PWPage', 'New-DiagramAnalysisPage'
)
