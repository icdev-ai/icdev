# pages/DiagramAnalysisPage.ps1
# Page object for /network/diagram-analysis.
# Wraps upload, industry selection, AI analysis, result injection,
# tab navigation, draw.io export, and HTML report download.
# All state is stored in $this.Context so assertions can inspect it after actions.

function New-DiagramAnalysisPage {
    param([hashtable]$Context)

    $actions = @{

        # Navigate to the diagram analysis page
        Navigate = {
            $url = "$($this.Context.Config.BaseUrl)/network/diagram-analysis"
            Open-PlaywrightPageUrl -Url $url -Page $this.Context.Page
            Start-Sleep -Seconds 2
        }

        # Upload a diagram file via multipart REST. Stores upload_id in Context.
        UploadDiagram = {
            param([string]$FilePath = "")
            if (-not $FilePath) { $FilePath = $this.Context.Config.DefaultDiagramPath }
            if (-not (Test-Path $FilePath)) { throw "UploadDiagram: file not found: $FilePath" }

            $url  = "$($this.Context.Config.BaseUrl)/network/api/diagram-upload"
            $data = Invoke-PWMultipartUpload -Context $this.Context -Url $url -FilePath $FilePath
            $this.Context.UploadId = $data.upload_id
            Write-Host ("      upload_id={0}  pages={1}" -f $data.upload_id, $data.page_count) -ForegroundColor DarkCyan
        }

        # Click the industry card in the browser UI (cosmetic / visual verification).
        SelectIndustry = {
            param([string]$Industry = "")
            if (-not $Industry) { $Industry = $this.Context.Config.DefaultIndustry }
            $js = "var c=document.querySelector('.da-industry-card[data-industry=""$Industry""]');if(c){c.click();true;}else{false;}"
            $ok = Invoke-PlaywrightPageJavascript -Expression $js -Page $this.Context.Page
            if (-not $ok) { throw "SelectIndustry: card '$Industry' not found on page" }
            Start-Sleep -Milliseconds 400
        }

        # POST to the analysis endpoint; spinner while LLM runs. Stores results in Context.
        RunAnalysis = {
            param([string]$Industry = "")
            if (-not $this.Context.UploadId) { throw "RunAnalysis: call UploadDiagram first" }
            if (-not $Industry) { $Industry = $this.Context.Config.DefaultIndustry }

            $url    = "$($this.Context.Config.BaseUrl)/network/api/diagram-analysis/$($this.Context.UploadId)/analyze"
            $body   = @{ industry = $Industry }
            $result = Invoke-PWJSON -Context $this.Context -Url $url -Method "POST" -Body $body -Label "LLM analysing diagram"

            $this.Context.AnalysisId     = $result.analysis_id
            $this.Context.Tabs           = $result.tabs
            $this.Context.CloudContext   = $result.cloud_context
            $this.Context.AnalysisResult = $result

            Write-Host ("      analysis_id={0}  mode={1}" -f $result.analysis_id, $result.cloud_context.mode) -ForegroundColor DarkCyan
        }

        # Inject analysis results into the live browser page via JS so the UI renders them.
        InjectResults = {
            if (-not $this.Context.AnalysisId) { throw "InjectResults: call RunAnalysis first" }

            $tabs  = ($this.Context.Tabs        | ConvertTo-Json -Depth 10 -Compress) -replace "'", "\'"
            $cc    = ($this.Context.CloudContext | ConvertTo-Json -Depth 5  -Compress) -replace "'", "\'"
            $anaId = $this.Context.AnalysisId
            $upId  = $this.Context.UploadId

            $js = "window.daAnalysisId='$anaId';window.daUploadId='$upId';" +
                  "var _d={analysis_id:'$anaId',tabs:$tabs,cloud_context:$cc};" +
                  "document.getElementById('daBtnHtmlReport').disabled=false;" +
                  "daRenderResults(_d);"

            Invoke-PlaywrightPageJavascript -Expression $js -Page $this.Context.Page
            Start-Sleep -Seconds 1
        }

        # Switch to a named analysis tab and scroll it into view.
        SwitchTab = {
            param([string]$TabName)
            $validTabs = @("overview","inventory","topology","security","compliance","remediate")
            if ($TabName -notin $validTabs) {
                throw "SwitchTab: '$TabName' is not valid. Use: $($validTabs -join ', ')"
            }
            Invoke-PlaywrightPageJavascript -Expression "daShowTab('$TabName')" -Page $this.Context.Page
            Start-Sleep -Milliseconds 500
            Invoke-PlaywrightPageJavascript -Expression "document.getElementById('daResults').scrollIntoView({behavior:'instant',block:'start'})" -Page $this.Context.Page
            Start-Sleep -Milliseconds 200
        }

        # Export the annotated draw.io diagram to $OutPath. Returns bytes saved.
        ExportDrawio = {
            param([string]$OutPath = "")
            if (-not $this.Context.AnalysisId) { throw "ExportDrawio: call RunAnalysis first" }
            if (-not $OutPath) {
                $ts     = Get-Date -Format "yyyyMMdd_HHmmss"
                $OutPath = Join-Path $this.Context.Config.ExportDir "ndc_remediated_${ts}.drawio"
            }
            $url   = "$($this.Context.Config.BaseUrl)/network/api/diagram-analysis/$($this.Context.AnalysisId)/export-drawio"
            $bytes = Save-PWBinary -Context $this.Context -Url $url -Method "POST" -Body "{}" -OutPath $OutPath -Label "Exporting draw.io"
            Write-Host ("      draw.io saved ({0} KB): {1}" -f [Math]::Round($bytes/1KB,1), $OutPath) -ForegroundColor DarkCyan
            return $OutPath
        }

        # Download the standalone HTML report to $OutPath. Returns path.
        DownloadReport = {
            param([string]$OutPath = "")
            if (-not $this.Context.AnalysisId) { throw "DownloadReport: call RunAnalysis first" }
            if (-not $OutPath) {
                $ts      = Get-Date -Format "yyyyMMdd_HHmmss"
                $OutPath = Join-Path $this.Context.Config.ExportDir "ndc_report_${ts}.html"
            }
            $url   = "$($this.Context.Config.BaseUrl)/network/api/diagram-analysis/$($this.Context.AnalysisId)/report.html"
            $bytes = Save-PWBinary -Context $this.Context -Url $url -Method "GET" -OutPath $OutPath -Label "Downloading HTML report"
            Write-Host ("      HTML report saved ({0} KB): {1}" -f [Math]::Round($bytes/1KB,1), $OutPath) -ForegroundColor DarkCyan
            return $OutPath
        }

        # Convenience: run full pipeline (upload + analyse + inject) in one call.
        RunFullPipeline = {
            param([string]$FilePath = "", [string]$Industry = "")
            $this.UploadDiagram($FilePath)
            $this.SelectIndustry($Industry)
            $this.RunAnalysis($Industry)
            $this.InjectResults()
        }
    }

    return New-PWPage -Context $Context `
        -Name    "DiagramAnalysis" `
        -BaseUrl "$($Context.Config.BaseUrl)/network/diagram-analysis" `
        -Actions $actions
}
