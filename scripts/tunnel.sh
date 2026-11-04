#!/usr/bin/env bash
# Показ без сервера: урок из MAX через туннели Cloudflare на локальный стек (#75).
#
#   ./scripts/tunnel.sh
#
# Поднимает два быстрых туннеля Cloudflare — на приложение (localhost:8000) и на
# LiveKit (localhost:7880), пишет адрес LiveKit в .env, выключает DEV_AUTH,
# перезапускает app и печатает строку для config.js в репозитории sferum-ai.
# Дальше следит за туннелями: умер или перестал отвечать — поднимает заново
# и снова обновляет адреса. Ctrl+C закрывает туннели и возвращает .env как было.
#
# cloudflared берётся из системы, а если его нет — из Docker-образа.
# Нужен bash 4+ (Linux; на Windows — scripts/tunnel.ps1).
# Звук идёт мимо туннеля, напрямую на LIVEKIT_NODE_IP: только в той же Wi-Fi-сети.

set -euo pipefail

cd "$(dirname "$0")/.."

APP_PORT=8000
LIVEKIT_PORT=7880
IMAGE=cloudflare/cloudflared:2025.11.1
STATE=.tunnel           # логи туннелей, в git не идёт
CHECK_EVERY_S=15
FAILS_TO_RESTART=3      # столько проверок подряд без ответа — туннель поднимаем заново

[ -f .env ] || { echo "Нет .env: cp .env.example .env и впишите LIVEKIT_NODE_IP" >&2; exit 1; }
grep -q '^LIVEKIT_NODE_IP=..*' .env || { echo "В .env нет LIVEKIT_NODE_IP — без него в звонке не будет звука" >&2; exit 1; }
mkdir -p "$STATE"

env_get() { grep -m1 "^$1=" .env | cut -d= -f2- || true; }

env_set() {
    # заменить строку KEY=… или дописать её; остальной .env не трогаем
    local tmp
    tmp=$(mktemp)
    awk -v key="$1" -v value="$2" '
        index($0, key "=") == 1 { print key "=" value; done = 1; next }
        { print }
        END { if (!done) print key "=" value }
    ' .env > "$tmp"
    cat "$tmp" > .env
    rm -f "$tmp"
}

if command -v cloudflared >/dev/null; then
    IN_DOCKER=0
    run_cloudflared() { cloudflared tunnel --no-autoupdate --url "http://localhost:$2"; }
else
    echo "cloudflared не найден, запускаю его из Docker ($IMAGE)"
    IN_DOCKER=1
    run_cloudflared() {
        docker rm -f "lesson-tunnel-$1" >/dev/null 2>&1 || true
        docker run --rm --name "lesson-tunnel-$1" --add-host host.docker.internal:host-gateway "$IMAGE" \
            tunnel --no-autoupdate --url "http://host.docker.internal:$2"
    }
fi

declare -A PID URL FAILS

start_tunnel() {
    # $1 — имя (app, livekit), $2 — порт. Ждёт адрес *.trycloudflare.com в логе
    local name=$1 port=$2 log="$STATE/$1.log" url=""
    run_cloudflared "$name" "$port" > "$log" 2>&1 &
    PID[$name]=$!
    for _ in $(seq 60); do
        url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$log" | head -1 || true)
        [ -n "$url" ] && break
        kill -0 "${PID[$name]}" 2>/dev/null || break
        sleep 1
    done
    if [ -z "$url" ]; then
        echo "Туннель $name не поднялся, лог: $log" >&2
        tail -5 "$log" >&2
        return 1
    fi
    URL[$name]=$url
    FAILS[$name]=0
}

stop_tunnel() {
    local pid=${PID[$1]:-}
    [ -n "$pid" ] || return 0
    # контейнер останавливаем по имени: убитый docker run его не всегда гасит
    [ "$IN_DOCKER" = 1 ] && docker rm -f "lesson-tunnel-$1" >/dev/null 2>&1
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    PID[$1]=""
}

restart_app() {
    # env_file читается при создании контейнера, поэтому пересоздаём
    docker compose up -d --force-recreate app >/dev/null 2>&1
    for _ in $(seq 60); do
        curl -fsS "http://localhost:$APP_PORT/api/health" >/dev/null 2>&1 && return 0
        sleep 1
    done
    echo "Приложение не ответило после перезапуска: docker compose logs app" >&2
}

apply_livekit() {
    env_set LIVEKIT_PUBLIC_URL "wss://${URL[livekit]#https://}"
}

announce() {
    echo
    echo "Приложение: ${URL[app]}/"
    echo "LiveKit:    wss://${URL[livekit]#https://}"
    echo
    echo "Строка для config.js в репозитории sferum-ai:"
    echo "window.APP_URL = \"${URL[app]}/\";"
    echo
}

alive() {
    # процесс жив, в логе нет «Tunnel not found», и через туннель отвечает сервис
    local name=$1 path
    kill -0 "${PID[$name]}" 2>/dev/null || return 1
    grep -qi 'tunnel not found' "$STATE/$name.log" && return 1
    [ "$name" = app ] && path=/api/health || path=/
    curl -fsS --max-time 10 -o /dev/null "${URL[$name]}$path"
}

OLD_LIVEKIT_URL=$(env_get LIVEKIT_PUBLIC_URL)
OLD_DEV_AUTH=$(env_get DEV_AUTH)

cleanup() {
    trap - EXIT
    echo
    echo "Закрываю туннели и возвращаю .env"
    stop_tunnel app
    stop_tunnel livekit
    env_set LIVEKIT_PUBLIC_URL "${OLD_LIVEKIT_URL:-ws://localhost:$LIVEKIT_PORT}"
    env_set DEV_AUTH "${OLD_DEV_AUTH:-0}"
    restart_app
    echo "Верните в config.js прежний адрес, иначе бот откроет мёртвую ссылку"
}
# Ctrl+C выходит из скрипта, а уборка — на выходе. Если вызвать её прямо по INT,
# bash после неё вернётся в цикл слежения и снова поднимет туннели
trap cleanup EXIT
trap 'exit 130' INT TERM

docker compose up -d >/dev/null 2>&1
echo "Поднимаю туннели…"
start_tunnel app "$APP_PORT"
start_tunnel livekit "$LIVEKIT_PORT"
apply_livekit
# через туннель урок открывают из MAX: вход по данным запуска, без ?id=
env_set DEV_AUTH 0
restart_app
announce
echo "Слежу за туннелями. Ctrl+C — закрыть."

while true; do
    sleep "$CHECK_EVERY_S"
    for name in app livekit; do
        if alive "$name"; then
            FAILS[$name]=0
            continue
        fi
        FAILS[$name]=$(( FAILS[$name] + 1 ))
        kill -0 "${PID[$name]}" 2>/dev/null && ! grep -qi 'tunnel not found' "$STATE/$name.log" \
            && [ "${FAILS[$name]}" -lt "$FAILS_TO_RESTART" ] && continue
        echo "$(date +%H:%M:%S) туннель $name пропал, поднимаю заново"
        stop_tunnel "$name"
        until start_tunnel "$name" "$([ "$name" = app ] && echo $APP_PORT || echo $LIVEKIT_PORT)"; do
            sleep "$CHECK_EVERY_S"  # нет сети — пробуем, пока не появится
        done
        if [ "$name" = livekit ]; then
            apply_livekit
            restart_app
        fi
        announce
        [ "$name" = app ] && echo "Адрес приложения сменился: обновите config.js"
    done
done
