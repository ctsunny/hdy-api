# APIhdy V5 Native Supreme Server
$host.UI.RawUI.WindowTitle = "APIhdy V5 Supreme Server"

$StaticDir = Join-Path $PSScriptRoot "static"
$TargetBase = "https://www.szhdy.com"
$ListenUrl = "http://127.0.0.1:8080/"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($ListenUrl)

try {
    $listener.Start()
} catch {
    Write-Host "CRITICAL ERROR: Could not start listener on 8080." -ForegroundColor Red
    Write-Host "Please ensure no other programs (Python, earlier versions) are using this port." -ForegroundColor Yellow
    pause; exit
}

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "        APIhdy SUPREME NATIVE SERVER V5" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Service active at: $ListenUrl" -ForegroundColor Green
Write-Host "Keep this window open during use." -ForegroundColor Gray

# Open browser
Start-Process "http://127.0.0.1:8080"

while ($listener.IsListening) {
    try {
        $context = $listener.GetContext()
        $request = $context.Request
        $response = $context.Response
        $localPath = $request.Url.LocalPath

        if ($localPath -eq "/local_proxy_login") {
            # Proxy Login Handler
            $reader = New-Object System.IO.StreamReader($request.InputStream)
            $body = $reader.ReadToEnd()
            
            Write-Host "[LOGIN] Processing proxy request..." -ForegroundColor Cyan
            
            try {
                $proxyRes = Invoke-WebRequest -Uri "$TargetBase/zjmf_api_login" `
                    -Method Post -Body $body -ContentType "application/json" `
                    -ErrorAction Stop
                
                $resContent = $proxyRes.Content
                $buffer = [System.Text.Encoding]::UTF8.GetBytes($resContent)
                
                $response.ContentType = "application/json; charset=utf-8"
                $response.ContentLength64 = $buffer.Length
                $response.OutputStream.Write($buffer, 0, $buffer.Length)
            } catch {
                Write-Host "[ERROR] Proxy login failed: $($_.Exception.Message)" -ForegroundColor Red
                $response.StatusCode = 502
            }
        }
        else {
            # Static File Handler
            if ($localPath -eq "/") { $localPath = "/index.html" }
            $fullPath = Join-Path $StaticDir $localPath.TrimStart('/')

            if (Test-Path $fullPath -PathType Leaf) {
                $fileBytes = [System.IO.File]::ReadAllBytes($fullPath)
                
                if ($localPath.EndsWith(".js")) { $response.ContentType = "application/javascript" }
                elseif ($localPath.EndsWith(".css")) { $response.ContentType = "text/css" }
                elseif ($localPath.EndsWith(".html")) { $response.ContentType = "text/html; charset=utf-8" }
                
                $response.ContentLength64 = $fileBytes.Length
                $response.OutputStream.Write($fileBytes, 0, $fileBytes.Length)
            } else {
                $response.StatusCode = 404
            }
        }
        $response.Close()
    } catch {
        # Catch unexpected request-specific errors to keep loop running
        if ($null -ne $response) { $response.Close() }
    }
}
