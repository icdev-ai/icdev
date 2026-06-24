# core/Http.ps1
# HTTP helpers built on System.Net.Http.HttpClient (PS 5.1, air-gap, binary-safe).
# All functions take a $Context hashtable and reuse Context.HttpClient.
# Spinner is provided by Wait-PWTask (Browser.ps1), loaded before this file.

function New-PWHttpClient {
    param([int]$TimeoutSec = 240)
    $c = New-Object System.Net.Http.HttpClient
    $c.Timeout = [System.TimeSpan]::FromSeconds($TimeoutSec)
    return $c
}

function _PW-ContentTypeForFile {
    param([string]$Path)
    switch ([System.IO.Path]::GetExtension($Path).ToLower()) {
        ".png"    { return "image/png" }
        ".jpg"    { return "image/jpeg" }
        ".jpeg"   { return "image/jpeg" }
        ".pdf"    { return "application/pdf" }
        ".drawio" { return "application/xml" }
        ".xml"    { return "application/xml" }
        ".docx"   { return "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }
        default   { return "application/octet-stream" }
    }
}

function Invoke-PWMultipartUpload {
    # Upload a local file as multipart/form-data. Returns parsed JSON response.
    param(
        [hashtable]$Context,
        [string]$Url,
        [string]$FilePath,
        [string]$FieldName   = "file",
        [string]$Label       = ""
    )
    if (-not (Test-Path $FilePath)) { throw "File not found: $FilePath" }

    $bytes    = [System.IO.File]::ReadAllBytes($FilePath)
    $fname    = [System.IO.Path]::GetFileName($FilePath)
    $ct       = _PW-ContentTypeForFile -Path $FilePath
    $sizeKB   = [Math]::Round($bytes.Length / 1KB, 1)
    if (-not $Label) { $Label = "Uploading $fname ($sizeKB KB)" }

    $multi    = New-Object System.Net.Http.MultipartFormDataContent
    $content  = [System.Net.Http.ByteArrayContent]::new($bytes)
    $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new($ct)
    $multi.Add($content, $FieldName, $fname)

    $task = $Context.HttpClient.PostAsync($Url, $multi)
    $resp = Wait-PWTask -Task $task -Label $Label

    if (-not $resp.IsSuccessStatusCode) {
        $body = $resp.Content.ReadAsStringAsync().Result
        throw "Upload failed ($($resp.StatusCode)): $body"
    }
    return $resp.Content.ReadAsStringAsync().Result | ConvertFrom-Json
}

function Invoke-PWJSON {
    # POST or GET JSON. Returns parsed response object.
    param(
        [hashtable]$Context,
        [string]$Url,
        [string]$Method      = "GET",
        [object]$Body        = $null,
        [string]$Label       = ""
    )
    if (-not $Label) { $Label = "$Method $($Url.Split('/')[-1])" }

    if ($Method -eq "GET" -or $null -eq $Body) {
        $task = $Context.HttpClient.GetAsync($Url)
    } else {
        $json    = if ($Body -is [string]) { $Body } else { ConvertTo-Json $Body -Compress -Depth 10 }
        $payload = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, "application/json")
        $task    = $Context.HttpClient.PostAsync($Url, $payload)
    }

    $resp = Wait-PWTask -Task $task -Label $Label

    if (-not $resp.IsSuccessStatusCode) {
        $errBody = $resp.Content.ReadAsStringAsync().Result
        throw "HTTP $Method failed ($($resp.StatusCode)): $errBody"
    }
    return $resp.Content.ReadAsStringAsync().Result | ConvertFrom-Json
}

function Save-PWBinary {
    # Download binary content and write to disk. Returns byte count saved.
    param(
        [hashtable]$Context,
        [string]$Url,
        [string]$Method      = "GET",
        [object]$Body        = $null,
        [string]$OutPath,
        [string]$Label       = ""
    )
    if (-not $Label) { $Label = "Downloading $(Split-Path $OutPath -Leaf)" }

    if ($Method -eq "GET" -or $null -eq $Body) {
        $task = $Context.HttpClient.GetAsync($Url)
    } else {
        $json    = if ($Body -is [string]) { $Body } else { ConvertTo-Json $Body -Compress }
        $payload = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, "application/json")
        $task    = $Context.HttpClient.PostAsync($Url, $payload)
    }

    $resp = Wait-PWTask -Task $task -Label $Label

    if (-not $resp.IsSuccessStatusCode) {
        throw "Download failed ($($resp.StatusCode)): $Url"
    }

    $bytes  = $resp.Content.ReadAsByteArrayAsync().Result
    $outDir = Split-Path $OutPath -Parent
    if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force $outDir | Out-Null }
    [System.IO.File]::WriteAllBytes($OutPath, $bytes)
    return $bytes.Length
}
