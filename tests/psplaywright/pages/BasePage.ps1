# pages/BasePage.ps1
# Page Object factory. New-PWPage returns a PSCustomObject with ScriptMethod members.
# Each action gets a built-in Navigate and Screenshot method automatically.
# Pass $Actions as a hashtable of name -> scriptblock; $this inside each block
# refers to the page object (which exposes $this.Context and $this.BaseUrl).

function New-PWPage {
    # Create a reusable page object for a given URL and set of named actions.
    param(
        [hashtable]$Context,
        [string]$Name,
        [string]$BaseUrl   = "",
        [hashtable]$Actions = @{}
    )

    $page = [PSCustomObject]@{
        Name    = $Name
        BaseUrl = $BaseUrl
        Context = $Context
    }

    # Built-in: Navigate to BaseUrl (or an override URL)
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Navigate" -Value {
        param([string]$Url = "")
        $target = if ($Url) { $Url } else { $this.BaseUrl }
        if (-not $target) { throw "Navigate: no URL provided for page '$($this.Name)'" }
        Open-PlaywrightPageUrl -Url $target -Page $this.Context.Page
        Start-Sleep -Milliseconds 1500
    }

    # Built-in: Take a named screenshot and return the path
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Screenshot" -Value {
        param([string]$Label = "")
        $ts   = Get-Date -Format "yyyyMMdd_HHmmss"
        $safe = ($this.Name + "_" + $Label) -replace '[^a-zA-Z0-9_-]', '_'
        $path = Join-Path $this.Context.Config.ScreenshotDir "${ts}_${safe}.png"
        try {
            Get-PlaywrightPageScreenshot -Path $path -Page $this.Context.Page
        } catch {}
        return $path
    }

    # Built-in: Wait for a selector to become visible
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "WaitFor" -Value {
        param([string]$Selector, [int]$TimeoutMs = 0)
        $ms  = if ($TimeoutMs -gt 0) { $TimeoutMs } else { $this.Context.Config.TimeoutMs }
        $loc = Find-PlaywrightPageElement -Selector $Selector -Page $this.Context.Page
        Invoke-PlaywrightLocatorAdvanced -Locator $loc -WaitFor -State Visible -Timeout $ms
    }

    # Built-in: Click a selector
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Click" -Value {
        param([string]$Selector)
        $loc = Find-PlaywrightPageElement -Selector $Selector -Page $this.Context.Page
        Invoke-PlaywrightLocatorClick -Locator $loc
        Start-Sleep -Milliseconds 300
    }

    # Built-in: Fill a text input
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Fill" -Value {
        param([string]$Selector, [string]$Value)
        $loc = Find-PlaywrightPageElement -Selector $Selector -Page $this.Context.Page
        Set-PlaywrightLocatorInput -Locator $loc -Value $Value
    }

    # Built-in: Evaluate JS and return the result
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Eval" -Value {
        param([string]$Expression)
        return Invoke-PlaywrightPageJavascript -Expression $Expression -Page $this.Context.Page
    }

    # Built-in: Get page title
    Add-Member -InputObject $page -MemberType ScriptMethod -Name "Title" -Value {
        return Get-PlaywrightPageTitle -Page $this.Context.Page
    }

    # User-defined actions added directly (no wrapper — Invoke-PWIt handles screenshots on failure)
    foreach ($key in $Actions.Keys) {
        Add-Member -InputObject $page -MemberType ScriptMethod -Name $key -Value $Actions[$key]
    }

    return $page
}
