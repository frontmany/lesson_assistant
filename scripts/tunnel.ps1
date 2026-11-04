# Показ без сервера: урок из MAX через туннели Cloudflare на локальный стек (#75).
#
#   powershell -ExecutionPolicy Bypass -File scripts\tunnel.ps1
#
# То же, что scripts/tunnel.sh: два быстрых туннеля Cloudflare — на приложение
# (localhost:8000) и на LiveKit (localhost:7880), адрес LiveKit в .env, DEV_AUTH=0,
# перезапуск app и строка для config.js в репозитории sferum-ai. Следит за
# туннелями и поднимает их заново. Ctrl+C закрывает туннели и возвращает .env.
#
# cloudflared берётся из системы (winget install Cloudflare.cloudflared),
# а если его нет — из Docker-образа. Звук идёт мимо туннеля, напрямую на
# LIVEKIT_NODE_IP: только в той же Wi-Fi-сети.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$AppPort = 8000
$LiveKitPort = 7880
$Image = "cloudflare/cloudflared:2025.11.1"
$State = ".tunnel"            # логи туннелей, в git не идёт
$CheckEverySec = 15
$FailsToRestart = 3           # столько проверок подряд без ответа — туннель поднимаем заново
$Ports = @{ app = $AppPort; livekit = $LiveKitPort }

if (-not (Test-Path .env)) { throw "Нет .env: скопируйте .env.example в .env и впишите LIVEKIT_NODE_IP" }
if (-not (Select-String -Path .env -Pattern '^LIVEKIT_NODE_IP=.+' -Quiet)) {
    throw "В .env нет LIVEKIT_NODE_IP — без него в звонке не будет звука"
}
if (-not (Select-String -Path .env -Pattern '^MAX_BOT_TOKEN=.+' -Quiet)) {
    throw "В .env нет MAX_BOT_TOKEN — без него сервер не проверит вход из MAX и ответит «Не удалось проверить данные MAX»"
}
New-Item -ItemType Directory -Force $State | Out-Null

function Get-EnvValue($key) {
    $line = Get-Content .env | Where-Object { $_.StartsWith("$key=") } | Select-Object -First 1
    if ($line) { $line.Substring($key.Length + 1) } else { $null }
}

function Set-EnvValue($key, $value) {
    # заменить строку KEY=… или дописать её; UTF-8 без BOM, иначе compose прочтёт первый ключ с мусором
    $lines = @(Get-Content .env)
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
    if (Test-Path $log) { Remove-Item $log }
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
    # контейнер останавливаем по имени: убитый docker run его не гасит
    if ($InDocker) { docker rm -f "lesson-tunnel-$name" 2>$null | Out-Null }
    if (-not $Proc[$name].HasExited) { Stop-Process -Id $Proc[$name].Id -Force -ErrorAction SilentlyContinue }
    $Proc[$name] = $null
}

function Restart-App {
    # env_file читается при создании контейнера, поэтому пересоздаём
    docker compose up -d --force-recreate app 2>$null | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest "http://localhost:$AppPort/api/health" -UseBasicParsing -TimeoutSec 3 | Out-Null; return }
        catch { Start-Sleep 1 }
    }
    Write-Warning "Приложение не ответило после перезапуска: docker compose logs app"
}

function Set-LiveKitUrl { Set-EnvValue "LIVEKIT_PUBLIC_URL" ($Url.livekit -replace '^https://', 'wss://') }

function Show-Addresses {
    Write-Host ""
    Write-Host "Приложение: $($Url.app)/"
    Write-Host "LiveKit:    $($Url.livekit -replace '^https://', 'wss://')"
    Write-Host ""
    Write-Host "Строка для config.js в репозитории sferum-ai:"
    Write-Host "window.APP_URL = `"$($Url.app)/`";"
    Write-Host ""
}

function Test-Tunnel($name) {
    # процесс жив, в логе нет «Tunnel not found», и через туннель отвечает сервис
    if ($Proc[$name].HasExited) { return $false }
    if (Select-String -Path (Join-Path $State "$name.log") -Pattern 'tunnel not found' -Quiet) { return $false }
    $path = if ($name -eq "app") { "/api/health" } else { "/" }
    try { Invoke-WebRequest "$($Url[$name])$path" -UseBasicParsing -TimeoutSec 10 | Out-Null; return $true }
    catch { return $false }
}

$OldLiveKitUrl = Get-EnvValue "LIVEKIT_PUBLIC_URL"
$OldDevAuth = Get-EnvValue "DEV_AUTH"

try {
    docker compose up -d 2>$null | Out-Null
    Write-Host "Поднимаю туннели…"
    foreach ($name in "app", "livekit") {
        if (-not (Start-Tunnel $name)) { throw "Туннель $name не поднялся" }
    }
    Set-LiveKitUrl
    # через туннель урок открывают из MAX: вход по данным запуска, без ?id=
    Set-EnvValue "DEV_AUTH" "0"
    Restart-App
    Show-Addresses
    Write-Host "Слежу за туннелями. Ctrl+C — закрыть."

    while ($true) {
        Start-Sleep $CheckEverySec
        foreach ($name in "app", "livekit") {
            if (Test-Tunnel $name) { $Fails[$name] = 0; continue }
            $Fails[$name]++
            $dead = $Proc[$name].HasExited -or
                (Select-String -Path (Join-Path $State "$name.log") -Pattern 'tunnel not found' -Quiet)
            if (-not $dead -and $Fails[$name] -lt $FailsToRestart) { continue }
            Write-Host "$(Get-Date -Format HH:mm:ss) туннель $name пропал, поднимаю заново"
            Stop-Tunnel $name
            while (-not (Start-Tunnel $name)) { Start-Sleep $CheckEverySec }  # нет сети — пробуем, пока не появится
            if ($name -eq "livekit") { Set-LiveKitUrl; Restart-App }
            Show-Addresses
            if ($name -eq "app") { Write-Host "Адрес приложения сменился: обновите config.js" }
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
    Write-Host "Верните в config.js прежний адрес, иначе бот откроет мёртвую ссылку"
}
