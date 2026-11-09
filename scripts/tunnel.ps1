# Показ без сервера через туннели Cloudflare на локальный стек

param([switch]$UpdateConfig)

# Не Stop, потому что PowerShell 5.1 считает ошибкой любой вывод в stderr, а BOM в файле ему нужен для кодировки
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$AppPort = 8000
$LiveKitPort = 7880
$Image = "cloudflare/cloudflared:2025.11.1"
$State = ".tunnel"
$CheckEverySec = 15
$FailsToRestart = 3
$Ports = @{ app = $AppPort; livekit = $LiveKitPort }
$ConfigRepo = "frontmany/sferum-ai"

$CanPush = (gh api "repos/$ConfigRepo" --jq .permissions.push 2>$null) -eq "true"
if ($UpdateConfig -and -not $CanPush) {
    throw "-UpdateConfig: нужен gh с правом записи в $ConfigRepo (gh auth login)"
}

if (-not (Test-Path .env)) { throw "Нет .env: скопируйте .env.example в .env и впишите LIVEKIT_NODE_IP" }
if (-not (Select-String -Path .env -Pattern '^LIVEKIT_NODE_IP=.+' -Quiet)) {
    throw "В .env нет LIVEKIT_NODE_IP — без него в звонке не будет звука"
}
if (-not (Select-String -Path .env -Pattern '^MAX_BOT_TOKEN=.+' -Quiet)) {
    throw "В .env нет MAX_BOT_TOKEN — без него сервер не проверит вход из MAX и ответит «Не удалось проверить данные MAX»"
}
New-Item -ItemType Directory -Force $State | Out-Null

function Get-EnvValue($key) {
    $line = Get-Content .env -Encoding UTF8 | Where-Object { $_.StartsWith("$key=") } | Select-Object -First 1
    if ($line) { $line.Substring($key.Length + 1) } else { $null }
}

function Set-EnvValue($key, $value) {
    # файл без BOM, иначе compose прочтёт первый ключ с мусором
    $lines = @(Get-Content .env -Encoding UTF8)
    $found = $false
    $lines = foreach ($line in $lines) {
        if ($line.StartsWith("$key=")) { "$key=$value"; $found = $true } else { $line }
    }
    if (-not $found) { $lines += "$key=$value" }
    [IO.File]::WriteAllLines((Resolve-Path .env), [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}

$InDocker = -not (Get-Command cloudflared -ErrorAction SilentlyContinue)
if ($InDocker) { Write-Host "cloudflared не найден, запускаю его из Docker ($Image)" }

$Proc = @{}
$Url = @{}
$Fails = @{}

function Start-Tunnel($name) {
    $port = $Ports[$name]
    $log = Join-Path $State "$name.log"
    if (Test-Path $log) { Move-Item $log "$log.prev" -Force }
    if ($InDocker) {
        docker rm -f "lesson-tunnel-$name" 2>$null | Out-Null
        $argList = "run --rm --name lesson-tunnel-$name --add-host host.docker.internal:host-gateway $Image " +
                "tunnel --no-autoupdate --url http://host.docker.internal:$port"
        $Proc[$name] = Start-Process docker -ArgumentList $argList -NoNewWindow -PassThru `
            -RedirectStandardError $log -RedirectStandardOutput "$log.out"
    } else {
        $argList = "tunnel --no-autoupdate --url http://localhost:$port"
        $Proc[$name] = Start-Process cloudflared -ArgumentList $argList -NoNewWindow -PassThru `
            -RedirectStandardError $log -RedirectStandardOutput "$log.out"
    }
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 1
        $match = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) {
            $Url[$name] = $match.Matches[0].Value
            $Fails[$name] = 0
            return $true
        }
        if ($Proc[$name].HasExited) { break }
    }
    Write-Warning "Туннель $name не поднялся, лог: $log"
    return $false
}

function Stop-Tunnel($name) {
    if (-not $Proc[$name]) { return }
    # убитый docker run контейнер не гасит
    if ($InDocker) { docker rm -f "lesson-tunnel-$name" 2>$null | Out-Null }
    if (-not $Proc[$name].HasExited) { Stop-Process -Id $Proc[$name].Id -Force -ErrorAction SilentlyContinue }
    $Proc[$name] = $null
}

function Restart-App {
    # env_file читается при создании контейнера
    docker compose up -d --force-recreate app 2>$null | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest "http://localhost:$AppPort/api/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop | Out-Null; return }
        catch { Start-Sleep 1 }
    }
    Write-Warning "Приложение не ответило после перезапуска: docker compose logs app"
}

function Set-LiveKitUrl { Set-EnvValue "LIVEKIT_PUBLIC_URL" ($Url.livekit -replace '^https://', 'wss://') }

function Get-ConfigFile {
    $file = gh api "repos/$ConfigRepo/contents/config.js" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw "gh не смог прочитать config.js" }
    $file | Add-Member text ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(($file.content -replace '\s', ''))))
    $file
}

function Get-ConfigUrl {
    if ((Get-ConfigFile).text -match '(?m)^window\.APP_URL = "(.*)";') { $Matches[1] } else { "check.html" }
}

function Set-ConfigUrl($url, $reason) {
    $file = Get-ConfigFile
    $old = $file.text
    $new = $old -replace '(?m)^window\.APP_URL = ".*";', "window.APP_URL = `"$url`";"
    if ($new -eq $old) { return }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($new))
    gh api -X PUT "repos/$ConfigRepo/contents/config.js" -f "message=Временно: адрес туннеля ($reason)" `
        -f "content=$encoded" -f "sha=$($file.sha)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "gh не смог обновить config.js" }
    Write-Host "config.js в ${ConfigRepo}: $url — бот откроет его через минуту"
}

function Show-Addresses {
    Write-Host ""
    Write-Host "Приложение: $($Url.app)/"
    Write-Host "LiveKit:    $($Url.livekit -replace '^https://', 'wss://')"
    Write-Host ""
    if ($UpdateConfig) {
        try { Set-ConfigUrl "$($Url.app)/" "scripts/tunnel.ps1" } catch { Write-Warning "Не удалось обновить config.js, впишите вручную" }
    } else {
        Write-Host "Строка для config.js в репозитории sferum-ai:"
        Write-Host "window.APP_URL = `"$($Url.app)/`";"
        if ($CanPush) { Write-Host "(доступ к $ConfigRepo есть: с -UpdateConfig скрипт впишет её сам)" }
    }
    Write-Host ""
}

function Test-Tunnel($name) {
    if ($Proc[$name].HasExited) { return $false }
    if (Select-String -Path (Join-Path $State "$name.log") -Pattern 'tunnel not found' -Quiet) { return $false }
    $path = if ($name -eq "app") { "/api/health" } else { "/" }
    try { Invoke-WebRequest "$($Url[$name])$path" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

$OldLiveKitUrl = Get-EnvValue "LIVEKIT_PUBLIC_URL"
$OldDevAuth = Get-EnvValue "DEV_AUTH"
$OldConfigUrl = if ($UpdateConfig) { Get-ConfigUrl } else { $null }

try {
    docker compose up -d 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker compose up не прошёл: запущен ли Docker?" }
    Write-Host "Поднимаю туннели…"
    foreach ($name in "app", "livekit") {
        if (-not (Start-Tunnel $name)) { throw "Туннель $name не поднялся" }
    }
    Set-LiveKitUrl
    Set-EnvValue "DEV_AUTH" "0"
    Restart-App
    Show-Addresses
    Write-Host "Слежу за туннелями. Ctrl+C — закрыть."

    while ($true) {
        Start-Sleep $CheckEverySec
        foreach ($name in "app", "livekit") {
            if (Test-Tunnel $name) { $Fails[$name] = 0; continue }
            $Fails[$name]++
            if ($Proc[$name].HasExited) { $reason = "cloudflared завершился" }
            elseif (Select-String -Path (Join-Path $State "$name.log") -Pattern 'tunnel not found' -Quiet) { $reason = "Cloudflare: Tunnel not found" }
            elseif ($Fails[$name] -ge $FailsToRestart) { $reason = "не отвечает $($Fails[$name]) проверки подряд" }
            else { continue }
            Write-Host "$(Get-Date -Format HH:mm:ss) туннель $name пропал ($reason), поднимаю заново; прошлый лог — $State\$name.log.prev"
            Stop-Tunnel $name
            while (-not (Start-Tunnel $name)) { Start-Sleep $CheckEverySec }
            if ($name -eq "livekit") { Set-LiveKitUrl; Restart-App }
            Show-Addresses
            if ($name -eq "app" -and -not $UpdateConfig) { Write-Host "Адрес приложения сменился: обновите config.js" }
        }
    }
}
finally {
    Write-Host ""
    Write-Host "Закрываю туннели и возвращаю .env"
    Stop-Tunnel "app"
    Stop-Tunnel "livekit"
    Set-EnvValue "LIVEKIT_PUBLIC_URL" $(if ($OldLiveKitUrl) { $OldLiveKitUrl } else { "ws://localhost:$LiveKitPort" })
    Set-EnvValue "DEV_AUTH" $(if ($OldDevAuth) { $OldDevAuth } else { "0" })
    Restart-App
    if ($UpdateConfig) {
        try { Set-ConfigUrl $OldConfigUrl "туннель закрыт" } catch { Write-Warning "Верните в config.js `"$OldConfigUrl`" вручную" }
    } else {
        Write-Host "Верните в config.js прежний адрес, иначе бот откроет мёртвую ссылку"
    }
}
