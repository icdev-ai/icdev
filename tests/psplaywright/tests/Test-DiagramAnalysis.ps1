# tests/Test-DiagramAnalysis.ps1
# E2E test suite for /network/diagram-analysis using the PSPlaywright framework.
# Runs standalone:  powershell -ExecutionPolicy Bypass -File .\tests\Test-DiagramAnalysis.ps1
# Or via runner:    .\Run-PSPlaywright.ps1 -Filter "DiagramAnalysis"
# A shared $ctx is injected by Run-PSPlaywright.ps1 via $Global:PWTestContext;
# when run standalone this file creates its own context and session.

# ---- Bootstrap --------------------------------------------------------------
$fw = Join-Path $PSScriptRoot "..\PSPlaywright.psm1"
Import-Module $fw -Force

$configPath = Join-Path $PSScriptRoot "..\psplaywright.config.json"

# Use injected context from runner, or create a standalone one
if ($Global:PWTestContext) {
    $ctx = $Global:PWTestContext
    $standaloneSession = $false
} else {
    $ctx = New-PWContext -ConfigPath $configPath
    Start-PWSession -Context $ctx
    $standaloneSession = $true
}

$page = New-DiagramAnalysisPage -Context $ctx

# Paths for outputs produced during this suite
$drawioPath = ""
$htmlPath   = ""

# ============================================================
# Suite 1: Page load and UI structure
# ============================================================
Invoke-PWDescribe "Diagram Analysis - Page Load" -Context $ctx {

    Invoke-PWIt "Navigates to /network/diagram-analysis without error" {
        $page.Navigate()
        Assert-PWUrl -Context $ctx -Expected "/network/diagram-analysis"
    }

    Invoke-PWIt "Page title contains 'NDC'" {
        Assert-PWTitle -Context $ctx -Expected "NDC"
    }

    Invoke-PWIt "Upload dropzone is visible" {
        Assert-PWVisible -Context $ctx -Selector "#daDropzone"
    }

    Invoke-PWIt "Industry selector cards are rendered (at least 3)" {
        Assert-PWMinCount -Context $ctx -Selector ".da-industry-card" -Min 3
    }

    Invoke-PWIt "Analyze button starts disabled" {
        $disabled = Invoke-PlaywrightPageJavascript `
            -Expression "document.getElementById('daAnalyzeBtn').disabled" `
            -Page $ctx.Page
        Assert-PWTrue -Condition ([bool]$disabled) -Message "Analyze button should be disabled before upload"
    }

    Invoke-PWIt "DoD IL4 industry card exists" {
        Assert-PWMinCount -Context $ctx -Selector ".da-industry-card[data-industry='dod_il4']" -Min 1
    }

    Invoke-PWIt "Takes initial page screenshot" {
        $shot = $page.Screenshot("initial")
        Assert-PWFileExists -Path $shot -MinBytes 1000
    }
}

# ============================================================
# Suite 2: Upload
# ============================================================
Invoke-PWDescribe "Diagram Analysis - Upload" -Context $ctx {

    Invoke-PWIt "Uploads NIPR-SIPR diagram via REST API" {
        $page.UploadDiagram()
        Assert-PWTrue -Condition ($null -ne $ctx.UploadId -and $ctx.UploadId -ne "") `
            -Message "upload_id should be set after UploadDiagram"
    }

    Invoke-PWIt "upload_id is a non-empty string" {
        Assert-PWTrue -Condition ($ctx.UploadId.Length -gt 4) `
            -Message "upload_id too short: $($ctx.UploadId)"
    }

    Invoke-PWIt "Selects DoD IL4 industry card in browser UI" {
        $page.SelectIndustry("dod_il4")
        $active = Invoke-PlaywrightPageJavascript `
            -Expression "document.querySelector('.da-industry-card[data-industry=""dod_il4""]').classList.contains('active')" `
            -Page $ctx.Page
        Assert-PWTrue -Condition ([bool]$active) -Message "dod_il4 card should have class 'active'"
    }
}

# ============================================================
# Suite 3: AI analysis
# ============================================================
Invoke-PWDescribe "Diagram Analysis - AI Analysis" -Context $ctx {

    Invoke-PWIt "Runs AI analysis and returns an analysis_id" {
        $page.RunAnalysis("dod_il4")
        Assert-PWTrue -Condition ($null -ne $ctx.AnalysisId -and $ctx.AnalysisId -ne "") `
            -Message "analysis_id should be set after RunAnalysis"
    }

    Invoke-PWIt "Analysis returns at least 1 security finding" {
        $count = if ($ctx.Tabs -and $ctx.Tabs.security) { $ctx.Tabs.security.Count } else { 0 }
        Assert-PWTrue -Condition ($count -ge 1) `
            -Message "Expected >=1 security findings, got $count"
    }

    Invoke-PWIt "Analysis returns at least 1 remediation action" {
        $count = if ($ctx.Tabs -and $ctx.Tabs.remediate) { $ctx.Tabs.remediate.Count } else { 0 }
        Assert-PWTrue -Condition ($count -ge 1) `
            -Message "Expected >=1 remediation items, got $count"
    }

    Invoke-PWIt "Analysis returns overview items" {
        $count = if ($ctx.Tabs -and $ctx.Tabs.overview) { $ctx.Tabs.overview.Count } else { 0 }
        Assert-PWTrue -Condition ($count -ge 1) `
            -Message "Expected >=1 overview items, got $count"
    }

    Invoke-PWIt "Cloud context has a topology mode" {
        $mode = if ($ctx.CloudContext) { $ctx.CloudContext.mode } else { "" }
        Assert-PWTrue -Condition ($mode -ne "") -Message "cloud_context.mode should not be empty"
    }
}

# ============================================================
# Suite 4: Browser results rendering
# ============================================================
Invoke-PWDescribe "Diagram Analysis - Results Rendering" -Context $ctx {

    Invoke-PWIt "Injects results into browser without JS error" {
        $page.InjectResults()
        Assert-PWVisible -Context $ctx -Selector "#daResults" -Because "results div should appear after inject"
    }

    Invoke-PWIt "Cloud banner is visible after inject" {
        Assert-PWVisible -Context $ctx -Selector "#daCloudBanner"
    }

    Invoke-PWIt "Takes overview screenshot" {
        $shot = $page.Screenshot("02_overview")
        Assert-PWFileExists -Path $shot -MinBytes 5000
    }

    Invoke-PWIt "All 6 tabs render content - inventory" {
        $page.SwitchTab("inventory")
        Assert-PWVisible -Context $ctx -Selector "#da-panel-inventory"
        $page.Screenshot("03_inventory") | Out-Null
    }

    Invoke-PWIt "All 6 tabs render content - topology" {
        $page.SwitchTab("topology")
        Assert-PWVisible -Context $ctx -Selector "#da-panel-topology"
        $page.Screenshot("04_topology") | Out-Null
    }

    Invoke-PWIt "All 6 tabs render content - security" {
        $page.SwitchTab("security")
        Assert-PWVisible -Context $ctx -Selector "#da-panel-security"
        $page.Screenshot("05_security") | Out-Null
    }

    Invoke-PWIt "Security tab contains at least one finding row" {
        $rowCount = Invoke-PlaywrightPageJavascript `
            -Expression "#daSecurityBody tr" `
            -Page $ctx.Page
        # Use JS querySelectorAll for count
        $count = Invoke-PlaywrightPageJavascript `
            -Expression "document.querySelectorAll('#daSecurityBody tr').length" `
            -Page $ctx.Page
        Assert-PWTrue -Condition ($count -ge 1) -Message "Security table has no rows"
    }

    Invoke-PWIt "All 6 tabs render content - compliance" {
        $page.SwitchTab("compliance")
        Assert-PWVisible -Context $ctx -Selector "#da-panel-compliance"
        $page.Screenshot("06_compliance") | Out-Null
    }

    Invoke-PWIt "All 6 tabs render content - remediate" {
        $page.SwitchTab("remediate")
        Assert-PWVisible -Context $ctx -Selector "#da-panel-remediate"
        $page.Screenshot("07_remediate") | Out-Null
    }

    Invoke-PWIt "Export draw.io button is visible" {
        Assert-PWVisible -Context $ctx -Selector ".da-btn-export"
    }

    Invoke-PWIt "HTML report button is enabled after analysis" {
        $disabled = Invoke-PlaywrightPageJavascript `
            -Expression "document.getElementById('daBtnHtmlReport').disabled" `
            -Page $ctx.Page
        Assert-PWTrue -Condition (-not [bool]$disabled) `
            -Message "HTML report button should be enabled after analysis"
    }
}

# ============================================================
# Suite 5: Export and download
# ============================================================
Invoke-PWDescribe "Diagram Analysis - Exports" -Context $ctx {

    Invoke-PWIt "draw.io export endpoint returns a file" {
        $script:drawioPath = $page.ExportDrawio()
        Assert-PWFileExists -Path $script:drawioPath -MinBytes 500 `
            -Because "draw.io XML should be at least 500 bytes"
    }

    Invoke-PWIt "draw.io export is valid XML containing mxGraphModel" {
        Assert-PWFileContains -Path $script:drawioPath -Expected "mxGraphModel" `
            -Because "Valid draw.io files always contain mxGraphModel"
    }

    Invoke-PWIt "draw.io export contains security annotation cells" {
        Assert-PWFileContains -Path $script:drawioPath -Expected "mxCell" `
            -Because "Annotated export must contain mxCell nodes"
    }

    Invoke-PWIt "HTML report endpoint returns a file" {
        $script:htmlPath = $page.DownloadReport()
        Assert-PWFileExists -Path $script:htmlPath -MinBytes 1000 `
            -Because "HTML report should be at least 1 KB"
    }

    Invoke-PWIt "HTML report contains remediation section" {
        Assert-PWFileContains -Path $script:htmlPath -Expected "Remediation Plan" `
            -Because "HTML report always includes remediation section heading"
    }

    Invoke-PWIt "HTML report contains Network Diagram Analysis heading" {
        Assert-PWFileContains -Path $script:htmlPath -Expected "Network Diagram Analysis Report"
    }

    Invoke-PWIt "Takes final screenshot" {
        $page.SwitchTab("overview")
        Invoke-PlaywrightPageJavascript -Expression "window.scrollTo(0,0)" -Page $ctx.Page
        $shot = $page.Screenshot("08_final")
        Assert-PWFileExists -Path $shot -MinBytes 5000
    }
}

# ============================================================
# Report + cleanup
# ============================================================
$junitPath = Join-Path $ctx.Config.JUnitDir "diagram-analysis.xml"
$allPassed = Write-PWReport -Context $ctx -JUnitPath $junitPath

if ($standaloneSession) {
    Stop-PWSession -Context $ctx
    exit $(if ($allPassed) { 0 } else { 1 })
}
