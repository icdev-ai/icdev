# core/Assert.ps1
# Assertion helpers for PSPlaywright tests. All functions throw on failure
# so Invoke-PWIt can catch them and record FAIL with the error message.
# Prefer these over raw try/catch in test bodies for readable failure messages.

function Assert-PWVisible {
    # Assert that a CSS selector is visible within $TimeoutMs.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [int]$TimeoutMs  = 0,
        [string]$Because = ""
    )
    $ms  = if ($TimeoutMs -gt 0) { $TimeoutMs } else { $Context.Config.TimeoutMs }
    $loc = Find-PlaywrightPageElement -Selector $Selector -Page $Context.Page
    try {
        Invoke-PlaywrightLocatorAdvanced -Locator $loc -WaitFor -State Visible -Timeout $ms
    } catch {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWVisible: '$Selector' is not visible after ${ms}ms${hint}"
    }
}

function Assert-PWNotVisible {
    # Assert that a CSS selector is hidden or detached.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [int]$TimeoutMs  = 0,
        [string]$Because = ""
    )
    $ms  = if ($TimeoutMs -gt 0) { $TimeoutMs } else { $Context.Config.TimeoutMs }
    $loc = Find-PlaywrightPageElement -Selector $Selector -Page $Context.Page
    try {
        Invoke-PlaywrightLocatorAdvanced -Locator $loc -WaitFor -State Hidden -Timeout $ms
    } catch {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWNotVisible: '$Selector' is still visible after ${ms}ms${hint}"
    }
}

function Assert-PWNotExist {
    # Assert that no element matches the selector (count == 0).
    param(
        [hashtable]$Context,
        [string]$Selector,
        [string]$Because = ""
    )
    $count = Invoke-PlaywrightPageJavascript -Expression "document.querySelectorAll('$Selector').length" -Page $Context.Page
    if ($count -gt 0) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWNotExist: '$Selector' matched $count element(s), expected none${hint}"
    }
}

function Assert-PWCount {
    # Assert that the number of elements matching $Selector equals $Expected.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [int]$Expected,
        [string]$Because = ""
    )
    $count = Invoke-PlaywrightPageJavascript -Expression "document.querySelectorAll('$Selector').length" -Page $Context.Page
    if ($count -ne $Expected) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWCount: '$Selector' matched $count element(s), expected $Expected${hint}"
    }
}

function Assert-PWMinCount {
    # Assert that at least $Min elements match the selector.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [int]$Min,
        [string]$Because = ""
    )
    $count = Invoke-PlaywrightPageJavascript -Expression "document.querySelectorAll('$Selector').length" -Page $Context.Page
    if ($count -lt $Min) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWMinCount: '$Selector' matched $count, expected at least $Min${hint}"
    }
}

function Assert-PWText {
    # Assert exact text content of the first matching element.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [string]$Expected,
        [string]$Because = ""
    )
    $loc    = Find-PlaywrightPageElement -Selector $Selector -Page $Context.Page
    $actual = Invoke-PlaywrightLocatorAdvanced -Locator $loc -Evaluate -Expression "el => (el.textContent || '').trim()"
    if ($actual -ne $Expected) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWText: '$Selector' text = '$actual', expected '$Expected'${hint}"
    }
}

function Assert-PWContainsText {
    # Assert that the first matching element's text contains $Expected.
    param(
        [hashtable]$Context,
        [string]$Selector,
        [string]$Expected,
        [string]$Because = ""
    )
    $loc    = Find-PlaywrightPageElement -Selector $Selector -Page $Context.Page
    $actual = Invoke-PlaywrightLocatorAdvanced -Locator $loc -Evaluate -Expression "el => el.textContent || ''"
    if ($actual -notlike "*$Expected*") {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWContainsText: '$Selector' does not contain '$Expected'${hint}"
    }
}

function Assert-PWPageContains {
    # Assert that the full page HTML contains $Expected.
    param(
        [hashtable]$Context,
        [string]$Expected,
        [string]$Because = ""
    )
    $html = Invoke-PlaywrightPageJavascript -Expression "document.documentElement.innerHTML" -Page $Context.Page
    if ($html -notlike "*$Expected*") {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWPageContains: page does not contain '$Expected'${hint}"
    }
}

function Assert-PWUrl {
    # Assert that the current page URL contains $Expected.
    param(
        [hashtable]$Context,
        [string]$Expected,
        [string]$Because = ""
    )
    $actual = Invoke-PlaywrightPageJavascript -Expression "window.location.href" -Page $Context.Page
    if ($actual -notlike "*$Expected*") {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWUrl: URL '$actual' does not contain '$Expected'${hint}"
    }
}

function Assert-PWTitle {
    # Assert that the page title contains $Expected.
    param(
        [hashtable]$Context,
        [string]$Expected,
        [string]$Because = ""
    )
    $title = Get-PlaywrightPageTitle -Page $Context.Page
    if ($title -notlike "*$Expected*") {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWTitle: title '$title' does not contain '$Expected'${hint}"
    }
}

function Assert-PWValue {
    # Assert two arbitrary values are equal. Use for API response checks.
    param(
        [object]$Actual,
        [object]$Expected,
        [string]$Label   = "value",
        [string]$Because = ""
    )
    if ($Actual -ne $Expected) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWValue: $Label = '$Actual', expected '$Expected'${hint}"
    }
}

function Assert-PWTrue {
    # Assert a boolean condition. Message is the failure text.
    param(
        [bool]$Condition,
        [string]$Message = "Assertion failed"
    )
    if (-not $Condition) { throw $Message }
}

function Assert-PWFileExists {
    # Assert a local file exists and optionally has a minimum size.
    param(
        [string]$Path,
        [int]$MinBytes   = 1,
        [string]$Because = ""
    )
    if (-not (Test-Path $Path)) {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWFileExists: '$Path' does not exist${hint}"
    }
    $size = (Get-Item $Path).Length
    if ($size -lt $MinBytes) {
        throw "Assert-PWFileExists: '$Path' is $size bytes, expected at least $MinBytes"
    }
}

function Assert-PWFileContains {
    # Assert a local file's text content contains $Expected.
    param(
        [string]$Path,
        [string]$Expected,
        [string]$Because = ""
    )
    if (-not (Test-Path $Path)) { throw "Assert-PWFileContains: '$Path' does not exist" }
    $text = Get-Content $Path -Raw
    if ($text -notlike "*$Expected*") {
        $hint = if ($Because) { " ($Because)" } else { "" }
        throw "Assert-PWFileContains: '$Path' does not contain '$Expected'${hint}"
    }
}
